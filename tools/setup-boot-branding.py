#!/usr/bin/env python3
"""Stage/install the Linux HDF boot cover. Does not update UEFI or initrd."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile

ROOT = Path(__file__).resolve().parents[1]
STATE = Path('/var/lib/hdf-vision/boot-branding')
EXTLINUX = Path('/boot/extlinux/extlinux.conf')
PLYMOUTH = Path('/etc/plymouth/plymouthd.conf')
THEME = Path('/usr/share/plymouth/themes/hdf-vision')
RELEASE = Path('/etc/nv_tegra_release')
BIOS = Path('/sys/class/dmi/id/bios_version')
LABEL = 'HDF_VISION_BRANDED'
FILES = ('brand.png', 'hdf-vision.plymouth', 'hdf-vision.script')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def branded_config(original):
    """Clone the active entry, preserving its kernel, initrd and DT overlays."""
    default = re.findall(r'^\s*DEFAULT\s+(\S+)\s*$', original, re.M | re.I)
    if len(default) != 1:
        raise ValueError('Boot konfigurácia musí mať práve jednu predvolenú položku.')
    entries = list(re.finditer(r'^LABEL\s+(\S+)[ \t]*$', original, re.M | re.I))
    if any(entry[1] == LABEL for entry in entries):
        raise ValueError('HDF boot položka už existuje. Najprv skontrolujte stav alebo obnovte pôvodný štart.')
    index = next((i for i, entry in enumerate(entries) if entry[1] == default[0]), None)
    if index is None:
        raise ValueError('Predvolená boot položka neexistuje.')
    start = entries[index].start()
    end = entries[index + 1].start() if index + 1 < len(entries) else len(original)
    block = original[start:end].rstrip()
    if len(re.findall(r'^\s*LINUX\s+', block, re.M | re.I)) != 1:
        raise ValueError('Neznáma boot položka bez jednoznačného jadra.')
    append = re.findall(r'^([ \t]*APPEND[ \t]+)([^\r\n]*)$', block, re.M | re.I)
    if len(append) != 1:
        raise ValueError('Boot položka musí mať práve jeden riadok APPEND.')
    tokens = append[0][1].split()
    owned = {'quiet', 'splash', 'console=tty0', 'plymouth.ignore-serial-consoles'}
    prefixes = ('loglevel=', 'systemd.show_status=', 'rd.systemd.show_status=',
                'udev.log_priority=', 'vt.global_cursor_default=')
    tokens = [token for token in tokens if token not in owned and not token.startswith(prefixes)]
    tokens += ['quiet', 'splash', 'loglevel=3', 'systemd.show_status=false',
               'rd.systemd.show_status=false', 'udev.log_priority=3',
               'vt.global_cursor_default=0', 'plymouth.ignore-serial-consoles']
    block = re.sub(r'^LABEL\s+\S+[^\r\n]*', 'LABEL ' + LABEL, block, count=1, flags=re.M | re.I)
    block = re.sub(r'^([ \t]*MENU LABEL)[^\r\n]*', r'\1 HDF Vision (logo, povodne jadro)', block, count=1, flags=re.M | re.I)
    block = re.sub(r'^[ \t]*APPEND[^\r\n]*', lambda _: append[0][0] + ' '.join(tokens), block, count=1, flags=re.M | re.I)
    result = re.sub(r'^(\s*DEFAULT\s+)\S+', lambda match: match[1] + LABEL, original, count=1, flags=re.M | re.I)
    return result.rstrip() + '\n\n' + block + '\n'


def write_atomic(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name + '.', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def stage(output):
    output.mkdir(parents=True, exist_ok=False)
    original = EXTLINUX.read_bytes()
    content = branded_config(original.decode()).encode()
    write_atomic(output / 'extlinux.conf', content)
    write_atomic(output / 'plymouthd.conf', b'[Daemon]\nTheme=hdf-vision\nShowDelay=0\nDeviceTimeout=8\n')
    for name in FILES:
        shutil.copy2(ROOT / 'tools/boot_branding' / name, output / name)
    metadata = dict(version=1, scope='Linux rootfs splash only; UEFI and initrd unchanged',
        release=RELEASE.read_text(),
        bios=BIOS.read_text().strip(),
        extlinux_before=digest(original),
        files={p.name: digest(p.read_bytes()) for p in output.iterdir() if p.is_file()})
    write_atomic(output / 'manifest.json', json.dumps(metadata, indent=2).encode())
    print('Pripravené:', output)
    print('Pôvodné jadro, initrd, parametre USB a pôvodné boot položky zostávajú zachované.')
    print('UEFI logo NVIDIA sa týmto krokom nemení. Načasovanie Linux splash treba overiť po reštarte.')


def require_root():
    if os.geteuid() != 0:
        raise RuntimeError('Inštalácia/obnova vyžaduje sudo alebo systémové overenie heslom.')


def install(source):
    require_root()
    metadata = json.loads((source / 'manifest.json').read_text())
    if metadata['release'] != RELEASE.read_text() or metadata['bios'] != BIOS.read_text().strip():
        raise RuntimeError('Zmenila sa verzia systému alebo bootloadera. Pripravte balík znova.')
    if digest(EXTLINUX.read_bytes()) != metadata['extlinux_before']:
        raise RuntimeError('Boot konfigurácia sa zmenila. Pripravte balík znova.')
    expected = set(FILES) | {'extlinux.conf', 'plymouthd.conf'}
    if set(metadata['files']) != expected:
        raise RuntimeError('Neúplný inštalačný balík.')
    for name, checksum in metadata['files'].items():
        if digest((source / name).read_bytes()) != checksum:
            raise RuntimeError('Balík má zmenený súbor: ' + name)
    if STATE.exists() or THEME.exists():
        raise RuntimeError('Záloha alebo cieľová téma už existuje. Nič nebolo prepísané.')
    STATE.mkdir(parents=True, mode=0o755)
    shutil.copy2(EXTLINUX, STATE / 'extlinux.original')
    if PLYMOUTH.exists():
        shutil.copy2(PLYMOUTH, STATE / 'plymouthd.original')
    destinations = {str(EXTLINUX): metadata['files']['extlinux.conf'],
        str(PLYMOUTH): metadata['files']['plymouthd.conf']}
    destinations.update({str(THEME / name): metadata['files'][name] for name in FILES})
    metadata.update(destinations=destinations, plymouth_existed=PLYMOUTH.exists())
    write_atomic(STATE / 'state.json', json.dumps(metadata, indent=2).encode())
    # Commit the new default last. A partial installation still boots the old entry.
    for name in FILES:
        write_atomic(THEME / name, (source / name).read_bytes())
    write_atomic(PLYMOUTH, (source / 'plymouthd.conf').read_bytes())
    write_atomic(EXTLINUX, (source / 'extlinux.conf').read_bytes())
    os.sync()
    print('Linux štartovacie logo nainštalované. Reštart nebol vykonaný.')
    print('Návrat: sudo python3 tools/setup-boot-branding.py restore')


def restore():
    require_root()
    metadata = json.loads((STATE / 'state.json').read_text())
    # Protect later manual changes. A partial installation may still have originals.
    allowed = {str(EXTLINUX): {metadata['extlinux_before']}}
    if metadata['plymouth_existed']:
        allowed[str(PLYMOUTH)] = {digest((STATE / 'plymouthd.original').read_bytes())}
    for filename, checksum in metadata['destinations'].items():
        path = Path(filename)
        if path.exists() and digest(path.read_bytes()) not in ({checksum} | allowed.get(filename, set())):
            raise RuntimeError('Súbor bol dodatočne zmenený, obnova zastavená: ' + filename)
    write_atomic(EXTLINUX, (STATE / 'extlinux.original').read_bytes())
    if metadata['plymouth_existed']:
        write_atomic(PLYMOUTH, (STATE / 'plymouthd.original').read_bytes())
    else:
        PLYMOUTH.unlink(missing_ok=True)
    for name in FILES:
        (THEME / name).unlink(missing_ok=True)
    if THEME.exists():
        THEME.rmdir()
    os.replace(STATE, STATE.with_name('boot-branding-restored-' + str(__import__('time').time_ns())))
    os.sync()
    print('Pôvodný štart bol obnovený. Reštart nebol vykonaný.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('stage').add_argument('--output', type=Path, required=True)
    commands.add_parser('install').add_argument('--staged', type=Path, required=True)
    commands.add_parser('restore')
    args = parser.parse_args()
    try:
        if args.command == 'stage': stage(args.output)
        elif args.command == 'install': install(args.staged)
        else: restore()
    except (OSError, ValueError, RuntimeError, KeyError) as exc:
        parser.exit(1, str(exc) + '\n')


if __name__ == '__main__':
    main()
