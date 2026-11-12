"""Conservative deployment for one verified Jetson BSP, using Python's stdlib."""
import argparse
import fcntl
import gzip
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
RECIPE = json.loads((HERE / 'recipe.json').read_text())
VALIDATION = json.loads((HERE / 'validation.json').read_text())
RELEASE = RECIPE['kernel_release']
STATE = Path('/var/lib/hdf-vision/cu55-usb-fix')
CONFIG = Path('/boot/extlinux/extlinux.conf')
IMAGE = Path('/boot/Image.hdf-cu55-usb-fix')
MODULE = Path('/lib/modules') / RELEASE / 'updates/hdf-cu55/uvcvideo.ko'
OPTIONS = Path('/etc/modprobe.d/zz-hdf-cu55-usb-fix.conf')
LABEL = 'HDF_CU55_USB_FIX'
BUILD_PACKAGES = (
    'build-essential', 'gcc-11', 'git', 'bc', 'flex', 'bison',
    'libssl-dev', 'libelf-dev', 'gcc-11-plugin-dev', 'kmod', 'patch',
    'initramfs-tools', 'openssl', 'ca-certificates',
)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def digest(path):
    return sha(Path(path).read_bytes())


def run(args, **kwargs):
    return subprocess.check_output([str(a) for a in args], text=True, **kwargs).strip()


def functional_config(data):
    return sha(('\n'.join(sorted(
        line for line in data.splitlines()
        if line.startswith('CONFIG_') and '=' in line
        and line.split('=')[0] not in ('CONFIG_CC_VERSION_TEXT', 'CONFIG_GCC_VERSION')
    )) + '\n').encode())


def abi(path):
    symbols = sorted((a[1], a[0]) for line in Path(path).read_text().splitlines()
                     if len(a := line.split()) >= 3)
    return len(symbols), sha(json.dumps(symbols, separators=(',', ':')).encode())


def headers():
    return Path('/usr/src') / ('linux-headers-' + RELEASE + '-ubuntu22.04_aarch64') / '3rdparty/canonical/linux-jammy/kernel-source'


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def doctor():
    require(platform.machine() == RECIPE['architecture'], 'Requires native aarch64 Jetson')
    require(platform.release() == RELEASE, 'Unsupported kernel release')
    model = Path('/proc/device-tree/model').read_text().strip('\0\n')
    require('Orin Nano' in model, 'Only Orin Nano is supported by this recipe')
    release = Path('/etc/nv_tegra_release').read_text()
    require(re.search(r'R36\b.*REVISION:\s*4\.7\b', release), 'Requires L4T 36.4.7')
    require(digest('/boot/Image') == RECIPE['original_kernel_sha256'], 'Factory kernel differs from reviewed BSP')
    stock = Path('/lib/modules') / RELEASE / 'kernel/drivers/media/usb/uvc/uvcvideo.ko'
    require(digest(stock) == RECIPE['original_uvc_sha256'], 'Factory UVC differs from reviewed BSP')
    config = gzip.decompress(Path('/proc/config.gz').read_bytes()).decode()
    require(functional_config(config) == RECIPE['functional_config_sha256'], 'Kernel configuration differs')
    require(abi(headers() / 'Module.symvers') == (RECIPE['abi_symbols'], RECIPE['abi_sha256']), 'Installed header ABI differs')
    for path in Path('/sys/firmware/efi/efivars').glob('SecureBoot-*'):
        require(path.read_bytes()[4:] == b'\0', 'Secure Boot needs a separately signed deployment')
    return {'recipe': RECIPE['id'], 'model': model, 'kernel': platform.version(),
            'validation': VALIDATION, 'compatible': True}


def idle():
    ref = Path('/sys/module/uvcvideo/refcnt')
    require(ref.exists() and ref.read_text().strip() == '0', 'Stop all camera applications first')


def root_only():
    require(os.geteuid() == 0, 'Run install/verify/rollback with sudo on the host')


def missing_build_packages():
    require(shutil.which('dpkg-query'), 'Automatic dependency setup requires dpkg-query')
    result = subprocess.run(
        ['dpkg-query', '-W', '-f=${Package}\t${Status}\n', *BUILD_PACKAGES],
        text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    # dpkg-query returns 1 when one or more requested packages are absent.
    require(result.returncode in (0, 1), 'Could not query build packages: ' + result.stderr.strip())
    installed = set()
    for line in result.stdout.splitlines():
        name, separator, status = line.partition('\t')
        if separator and status == 'install ok installed':
            installed.add(name)
    return [name for name in BUILD_PACKAGES if name not in installed]


def ensure_build_dependencies():
    missing = missing_build_packages()
    if not missing:
        return
    require(shutil.which('apt-get'), 'Automatic dependency setup requires apt-get')
    require(shutil.which('sudo'), 'Install the missing packages with an administrator: ' + ' '.join(missing))
    print('Installing missing build packages: ' + ', '.join(missing), flush=True)
    print('sudo may ask for your password; compilation continues as your ordinary user.', flush=True)
    # Inherit the terminal so sudo and apt can show progress and report failures.
    # Install only missing prerequisites; do not perform a system upgrade.
    subprocess.run(['sudo', '--', 'apt-get', 'update'], check=True)
    subprocess.run(['sudo', '--', 'apt-get', 'install', '--yes', '--no-install-recommends', *missing], check=True)
    remaining = missing_build_packages()
    require(not remaining, 'Dependency installation incomplete: ' + ', '.join(remaining))


def atomic(path, data, expected=None):
    """Replace only a known file; persist content before the directory entry."""
    path = Path(path)
    require(not path.is_symlink(), 'Refusing symlink: ' + str(path))
    if expected is not None:
        require(digest(path) == expected, 'File changed: ' + str(path))
    fd, temp = tempfile.mkstemp(prefix='.hdf-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temp, 0o644)
        if expected is not None:
            require(digest(path) == expected, 'File changed during preparation')
        os.replace(temp, path)
        sync_dir(path.parent)
    finally:
        Path(temp).unlink(missing_ok=True)


def sync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def exclusive(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    sync_dir(path.parent)


def boot_proposal(text):
    """Clone the default label, including board-specific FDT, initrd and overlays."""
    require(not re.search(r'^\s*LABEL\s+' + LABEL + r'\s*$', text, re.M), 'Boot label already exists')
    defaults = re.findall(r'^\s*DEFAULT\s+(\S+)\s*$', text, re.M | re.I)
    require(len(defaults) == 1, 'Expected exactly one DEFAULT boot label')
    entries = list(re.finditer(r'^\s*LABEL[ \t]+(\S+)[ \t]*$', text, re.M | re.I))
    matches = [i for i, match in enumerate(entries) if match[1] == defaults[0]]
    require(len(matches) == 1, 'Active boot entry is ambiguous')
    i = matches[0]
    block = text[entries[i].start():entries[i+1].start() if i+1 < len(entries) else len(text)]
    require(len(re.findall(r'^\s*LINUX\s+\S+', block, re.M | re.I)) == 1, 'Expected one LINUX directive')
    block = re.sub(r'^(\s*LABEL[ \t]+)\S+', lambda m: m[1] + LABEL, block, count=1, flags=re.M | re.I)
    block = re.sub(r'^(\s*LINUX[ \t]+)\S+', lambda m: m[1] + str(IMAGE), block, count=1, flags=re.M | re.I)
    block = re.sub(r'^\s*MENU LABEL[^\n]*', '\n      MENU LABEL HDF CU55 experimental USB repair', block, flags=re.M | re.I)
    updated = re.sub(r'^(\s*DEFAULT[ \t]+)\S+', lambda m: m[1] + LABEL, text, count=1, flags=re.M | re.I)
    return updated.rstrip() + '\n\n' + block.strip() + '\n'


def recipe_hash():
    return digest(HERE / 'recipe.json')


def artifacts(path):
    path = Path(path).resolve()
    manifest = json.loads((path / 'manifest.json').read_text())
    require(manifest['recipe_sha256'] == recipe_hash(), 'Artifacts were built with a different recipe')
    require(manifest['abi'] == [RECIPE['abi_symbols'], RECIPE['abi_sha256']], 'Artifact ABI not verified')
    require(set(manifest['files']) == {'Image', 'uvcvideo.ko'}, 'Unexpected artifact set')
    for name, checksum in manifest['files'].items():
        require(not (path / name).is_symlink() and digest(path / name) == checksum, 'Artifact changed: ' + name)
    require((path / 'Image').read_bytes()[56:60] == b'ARM\x64', 'Not an ARM64 Image')
    require(run(['modinfo', '-F', 'version', path / 'uvcvideo.ko']) == RECIPE['uvc_version'], 'Wrong UVC version')
    require(run(['modinfo', '-F', 'vermagic', path / 'uvcvideo.ko']).split()[0] == RELEASE, 'Wrong module ABI release')
    return path, manifest


def build(output):
    doctor()
    require(os.geteuid() != 0, 'Build as an ordinary user; only installation requires root')
    output = Path(output).expanduser().resolve()
    repository = HERE.parent.parent
    require(not output.is_relative_to(repository), 'Use a build directory outside the application repository')
    require(not output.exists(), 'Output exists; choose a fresh directory')
    require(shutil.disk_usage(output.parent).free > 12 * 1024**3, 'Build requires 12 GiB free space')
    ensure_build_dependencies()
    for command in ('git', 'make', 'gcc-11', 'bc', 'flex', 'bison', 'openssl', 'patch', 'modinfo'):
        require(shutil.which(command), 'Missing build dependency: ' + command)
    plugin = Path(run(['gcc-11', '-print-file-name=plugin'])) / 'include/gcc-plugin.h'
    require(plugin.is_file(), 'Install gcc-11-plugin-dev (the kernel config requires GCC_PLUGINS)')
    output.mkdir()
    src, obj = output / 'source', output / 'out'
    run(['git', 'clone', '--depth', '1', '--branch', RECIPE['source_tag'], RECIPE['source_repository'], src])
    require(run(['git', '-C', src, 'rev-parse', 'HEAD']) == RECIPE['source_commit'], 'Official source tag changed')
    for patch in RECIPE['patches']:
        file = HERE / 'patches' / patch['file']
        require(digest(file) == patch['sha256'], 'Patch integrity check failed')
        for name, checksums in patch['files'].items():
            require(digest(src / name) == checksums['before'], 'Unexpected source: ' + name)
        run(['patch', '-d', src, '-p1', '--batch', '--forward', '-i', file])
        for name, checksums in patch['files'].items():
            require(digest(src / name) == checksums['after'], 'Patched source mismatch: ' + name)
    obj.mkdir()
    (obj / '.config').write_bytes(gzip.decompress(Path('/proc/config.gz').read_bytes()))
    env = os.environ.copy()
    env.update(ARCH='arm64', CC='gcc-11', LOCALVERSION='-tegra', KBUILD_BUILD_VERSION=RECIPE['build_version'],
               KBUILD_BUILD_USER='hdf', KBUILD_BUILD_HOST='jetson')
    command = ['make', '-C', src, 'O=' + str(obj), 'ARCH=arm64', 'LOCALVERSION=-tegra']
    # Keep the full compiler log on disk rather than accumulating it in memory.
    with (output / 'build.log').open('w') as log:
        subprocess.run([str(x) for x in command + ['olddefconfig']], env=env, stdout=log, stderr=subprocess.STDOUT, check=True)
        require(functional_config((obj / '.config').read_text()) == RECIPE['functional_config_sha256'], 'Build changed functional kernel config')
        require(run(command + ['-s', 'kernelrelease'], env=env) == RELEASE, 'Build changed kernel release')
        subprocess.run([str(x) for x in ['nice', '-n', '10', *command, '-j3', 'Image', 'modules']], env=env, stdout=log, stderr=subprocess.STDOUT, check=True)
    exported = abi(obj / 'Module.symvers')
    require(exported == (RECIPE['abi_symbols'], RECIPE['abi_sha256']), 'Kernel export ABI changed; do not install')
    shutil.copyfile(obj / 'arch/arm64/boot/Image', output / 'Image')
    shutil.copyfile(obj / 'drivers/media/usb/uvc/uvcvideo.ko', output / 'uvcvideo.ko')
    manifest = dict(recipe_sha256=recipe_hash(), source_commit=RECIPE['source_commit'], abi=exported,
                    files={name: digest(output / name) for name in ('Image', 'uvcvideo.ko')})
    (output / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    artifacts(output)
    print(json.dumps({'built': str(output), 'validation': VALIDATION}, indent=2))


def install(path, experimental):
    root_only()
    doctor()
    idle()
    require(VALIDATION['state'] == 'validated' or experimental,
            'Validation incomplete. Only an explicit --experimental installation is available.')
    path, manifest = artifacts(path)
    require(not STATE.exists(), 'Existing deployment state; verify or roll back first')
    for target in (IMAGE, MODULE, OPTIONS):
        require(not os.path.lexists(target), 'Existing target; refusing overwrite: ' + str(target))
    require(not CONFIG.is_symlink(), 'Symlink boot configuration is not supported')
    original = CONFIG.read_bytes()
    proposed = boot_proposal(original.decode()).encode()
    # A stock module embedded in initrd would override the root filesystem copy.
    # This recipe preserves the vendor initrd and refuses such installations.
    for initrd in re.findall(r'^\s*INITRD[ \t]+(\S+)', original.decode(), re.M | re.I):
        contents = run(['lsinitramfs', initrd])
        require(not re.search(r'(^|/)uvcvideo\.ko(?:\.(?:xz|gz|zst))?$', contents, re.M),
                'Initrd embeds UVC; a separately reviewed initrd rebuild is required')
    params = {}
    for name in ('clock', 'hwtimestamps', 'trace', 'timeout', 'nodrop', 'quirks'):
        params[name] = (Path('/sys/module/uvcvideo/parameters') / name).read_text().strip()
        require(re.fullmatch(r'[A-Za-z0-9_]+', params[name]), 'Unexpected module parameter')
    content = {str(IMAGE): (path / 'Image').read_bytes(), str(MODULE): (path / 'uvcvideo.ko').read_bytes(),
               str(OPTIONS): ('options uvcvideo ' + ' '.join(k + '=' + v for k, v in params.items()) + '\n').encode()}
    receipt = dict(state='PREPARED', original=original.decode(), proposed=proposed.decode(),
                   files={name: sha(data) for name, data in content.items()}, recipe_sha256=recipe_hash(),
                   boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip(), experimental=experimental)
    STATE.mkdir(parents=True, mode=0o700)
    atomic(STATE / 'state.json', json.dumps(receipt, indent=2).encode())
    created = []
    try:
        for name, data in content.items():
            exclusive(name, data)
            created.append(Path(name))
        run(['depmod', '-a', RELEASE])
        require(Path(run(['modinfo', '-n', 'uvcvideo'])).resolve() == MODULE.resolve(), 'modprobe does not select installed UVC')
        atomic(CONFIG, proposed, sha(original))
        receipt['state'] = 'INSTALLED_REBOOT_REQUIRED'
        atomic(STATE / 'state.json', json.dumps(receipt, indent=2).encode())
    except BaseException:
        # Keep the persisted receipt even if cleanup is interrupted.
        if digest(CONFIG) == sha(proposed):
            atomic(CONFIG, original, sha(proposed))
        if digest(CONFIG) == sha(original):
            for target in reversed(created):
                if not target.is_symlink() and digest(target) == receipt['files'][str(target)]:
                    target.unlink()
            run(['depmod', '-a', RELEASE])
        raise
    print('Installed. Reboot manually, then run verify. No reboot or module reload was performed.')


def receipt():
    require(not (STATE / 'state.json').is_symlink(), 'Invalid deployment receipt')
    state = json.loads((STATE / 'state.json').read_text())
    require(state['recipe_sha256'] == recipe_hash(), 'Use the installer version that created this deployment')
    require(set(state['files']) == {str(IMAGE), str(MODULE), str(OPTIONS)}, 'Invalid receipt targets')
    return state


def verify():
    root_only()
    doctor()
    state = receipt()
    require(state['state'] == 'INSTALLED_REBOOT_REQUIRED', 'No installed deployment')
    require(Path('/proc/sys/kernel/random/boot_id').read_text().strip() != state['boot_id'], 'Reboot has not occurred')
    require('#' + RECIPE['build_version'] + ' ' in platform.version(), 'Patched kernel is not running')
    require(CONFIG.read_text() == state['proposed'], 'Boot configuration changed')
    for name, checksum in state['files'].items():
        require(not Path(name).is_symlink() and digest(name) == checksum, 'Installed file changed: ' + name)
    require(Path(run(['modinfo', '-n', 'uvcvideo'])).resolve() == MODULE.resolve(), 'Unexpected selected UVC path')
    require(Path('/sys/module/uvcvideo/version').read_text().strip() == RECIPE['uvc_version'], 'Patched UVC is not loaded')
    print(json.dumps({'deployment_verified': True, 'validation': VALIDATION,
                      'capture_test_performed': False}, indent=2))


def rollback():
    root_only()
    idle()
    state = receipt()
    require(CONFIG.read_text() in (state['original'], state['proposed']), 'Boot configuration changed; manual review required')
    for name, checksum in state['files'].items():
        target = Path(name)
        if target.exists() or target.is_symlink():
            require(not target.is_symlink() and digest(target) == checksum, 'Owned file changed; refusing removal: ' + name)
    if CONFIG.read_text() == state['proposed']:
        atomic(CONFIG, state['original'].encode(), sha(state['proposed'].encode()))
    for name in state['files']:
        Path(name).unlink(missing_ok=True)
    run(['depmod', '-a', RELEASE])
    state['state'] = 'ROLLED_BACK_REBOOT_REQUIRED'
    atomic(STATE / 'state.json', json.dumps(state, indent=2).encode())
    print('Boot selection and module lookup restored. Reboot manually; currently loaded code stays active until reboot.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('doctor')
    build_parser = commands.add_parser('build')
    build_parser.add_argument('--output', required=True)
    installer = commands.add_parser('install')
    installer.add_argument('--artifacts', required=True)
    installer.add_argument('--experimental', action='store_true')
    commands.add_parser('verify')
    commands.add_parser('rollback')
    args = parser.parse_args()
    try:
        if args.command == 'doctor':
            print(json.dumps(doctor(), indent=2))
        elif args.command == 'build':
            build(args.output)
        else:
            root_only()
            with open('/run/lock/hdf-cu55-usb-fix.lock', 'a') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                if args.command == 'install':
                    install(args.artifacts, args.experimental)
                elif args.command == 'verify':
                    verify()
                else:
                    rollback()
    except (RuntimeError, OSError, ValueError, KeyError, subprocess.CalledProcessError) as exc:
        parser.exit(1, str(exc) + '\n')
