"""Deployment failure guards, without touching camera, modules or host /boot."""
import importlib.util
import json
from pathlib import Path

import pytest

PATH = Path(__file__).resolve().parents[1] / 'tools/cu55_usb_fix_support/installer.py'
spec = importlib.util.spec_from_file_location('cu55_installer', PATH)
fix = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fix)

BOOT = '''TIMEOUT 30
DEFAULT JetsonIO
LABEL primary
 MENU LABEL primary
 LINUX /boot/Image
 INITRD /boot/initrd
 APPEND primary=1
LABEL JetsonIO
 MENU LABEL JetsonIO
 LINUX /boot/Image
 FDT /boot/dtb/board-super.dtb
 INITRD /boot/initrd
 APPEND root=UUID=abc quiet $cbootargs
 OVERLAYS /boot/jetson-test-gpio-overlay.dtbo
'''


def test_boot_clone_preserves_board_and_original_entries():
    result = fix.boot_proposal(BOOT)
    original, clone = result.split('LABEL HDF_CU55_USB_FIX')
    assert original.replace('DEFAULT HDF_CU55_USB_FIX', 'DEFAULT JetsonIO').strip() == BOOT.strip()
    for line in ('FDT /boot/dtb/board-super.dtb', 'INITRD /boot/initrd',
                 'APPEND root=UUID=abc quiet $cbootargs', 'OVERLAYS /boot/jetson-test-gpio-overlay.dtbo'):
        assert line in clone
    assert 'LINUX /boot/Image.hdf-cu55-usb-fix' in clone


@pytest.mark.parametrize('text', [BOOT.replace('DEFAULT JetsonIO', ''),
                                 BOOT + '\nDEFAULT primary\n',
                                 BOOT.replace('DEFAULT JetsonIO', 'DEFAULT missing'),
                                 BOOT + '\nLABEL HDF_CU55_USB_FIX\n LINUX /boot/unknown\n'])
def test_ambiguous_boot_configuration_is_rejected(text):
    with pytest.raises(RuntimeError):
        fix.boot_proposal(text)


def test_atomic_write_refuses_changed_file_and_symlink(tmp_path):
    target = tmp_path / 'extlinux.conf'
    target.write_text(BOOT)
    with pytest.raises(RuntimeError):
        fix.atomic(target, b'changed', 'incorrect hash')
    assert target.read_text() == BOOT
    link = tmp_path / 'link'
    link.symlink_to(target)
    with pytest.raises(RuntimeError):
        fix.atomic(link, b'changed')
    assert target.read_text() == BOOT
    fix.atomic(target, b'new', fix.sha(BOOT.encode()))
    assert target.read_bytes() == b'new'


def setup_host(monkeypatch, tmp_path):
    config = tmp_path / 'extlinux.conf'
    config.write_text(BOOT)
    paths = {'CONFIG': config, 'IMAGE': tmp_path / 'Image', 'MODULE': tmp_path / 'uvcvideo.ko',
             'OPTIONS': tmp_path / 'options.conf', 'STATE': tmp_path / 'state'}
    for name, path in paths.items():
        monkeypatch.setattr(fix, name, path)
    monkeypatch.setattr(fix, 'root_only', lambda: None)
    monkeypatch.setattr(fix, 'idle', lambda: None)
    return paths


def write_receipt(paths):
    files = {}
    for key in ('IMAGE', 'MODULE', 'OPTIONS'):
        paths[key].write_text(key)
        files[str(paths[key])] = fix.digest(paths[key])
    state = dict(state='INSTALLED_REBOOT_REQUIRED', recipe_sha256=fix.recipe_hash(), files=files,
                 original=BOOT, proposed=fix.boot_proposal(BOOT))
    paths['CONFIG'].write_text(state['proposed'])
    paths['STATE'].mkdir()
    (paths['STATE'] / 'state.json').write_text(json.dumps(state))
    return state


def test_rollback_checks_every_file_before_mutating(monkeypatch, tmp_path):
    paths = setup_host(monkeypatch, tmp_path)
    state = write_receipt(paths)
    paths['MODULE'].write_text('someone changed this')
    monkeypatch.setattr(fix, 'run', lambda args: pytest.fail('depmod must not run'))
    with pytest.raises(RuntimeError, match='Owned file changed'):
        fix.rollback()
    assert paths['CONFIG'].read_text() == state['proposed']
    assert paths['IMAGE'].exists()


def test_rollback_restores_boot_and_supports_partial_install(monkeypatch, tmp_path):
    paths = setup_host(monkeypatch, tmp_path)
    write_receipt(paths)
    paths['MODULE'].unlink()  # interrupted installation may not have created every target
    calls = []
    monkeypatch.setattr(fix, 'run', lambda args: calls.append(args))
    fix.rollback()
    assert paths['CONFIG'].read_text() == BOOT
    assert not any(paths[k].exists() for k in ('IMAGE', 'MODULE', 'OPTIONS'))
    assert calls == [['depmod', '-a', fix.RELEASE]]


def test_experimental_gate_precedes_any_host_write(monkeypatch, tmp_path):
    paths = setup_host(monkeypatch, tmp_path)
    monkeypatch.setattr(fix, 'doctor', lambda: {})
    with pytest.raises(RuntimeError, match='Validation incomplete'):
        fix.install(tmp_path / 'missing', experimental=False)
    assert not paths['STATE'].exists()
    assert paths['CONFIG'].read_text() == BOOT


def test_install_failure_restores_boot_and_only_owned_files(monkeypatch, tmp_path):
    paths = setup_host(monkeypatch, tmp_path)
    monkeypatch.setattr(fix, 'doctor', lambda: {})
    built = tmp_path / 'artifacts'
    built.mkdir()
    (built / 'Image').write_bytes(b'kernel')
    (built / 'uvcvideo.ko').write_bytes(b'module')
    monkeypatch.setattr(fix, 'artifacts', lambda path: (built, {}))
    # A real /sys parameter set is unnecessary for this filesystem transaction.
    original_read = Path.read_text
    monkeypatch.setattr(Path, 'read_text', lambda p, *a, **kw: '0' if str(p).startswith('/sys/module/uvcvideo/parameters/') else original_read(p, *a, **kw))
    calls = []
    def command(args):
        calls.append(args)
        if args[0] == 'lsinitramfs':
            return 'usr/lib/modules/factory.ko'
        if args[0] == 'modinfo':
            return str(tmp_path / 'wrong.ko')
        return ''
    monkeypatch.setattr(fix, 'run', command)
    with pytest.raises(RuntimeError, match='modprobe does not select'):
        fix.install(built, experimental=True)
    assert paths['CONFIG'].read_text() == BOOT
    assert not any(paths[k].exists() for k in ('IMAGE', 'MODULE', 'OPTIONS'))
    assert (paths['STATE'] / 'state.json').exists()  # crash recovery receipt survives


def test_patch_integrity_and_source_paths():
    for patch in fix.RECIPE['patches']:
        assert fix.digest(fix.HERE / 'patches' / patch['file']) == patch['sha256']
        for path in patch['files']:
            assert not Path(path).is_absolute() and '..' not in Path(path).parts


def test_artifact_tampering_is_rejected_before_modinfo(monkeypatch, tmp_path):
    (tmp_path / 'Image').write_bytes(b'changed')
    (tmp_path / 'uvcvideo.ko').write_bytes(b'module')
    (tmp_path / 'manifest.json').write_text(json.dumps(dict(
        recipe_sha256=fix.recipe_hash(), abi=[fix.RECIPE['abi_symbols'], fix.RECIPE['abi_sha256']],
        files={'Image': fix.sha(b'original'), 'uvcvideo.ko': fix.sha(b'module')})))
    monkeypatch.setattr(fix, 'run', lambda args: pytest.fail('Must reject before external command'))
    with pytest.raises(RuntimeError, match='Artifact changed'):
        fix.artifacts(tmp_path)


def test_initrd_with_stock_uvc_is_rejected_before_creating_state(monkeypatch, tmp_path):
    paths = setup_host(monkeypatch, tmp_path)
    monkeypatch.setattr(fix, 'doctor', lambda: {})
    monkeypatch.setattr(fix, 'artifacts', lambda path: (tmp_path, {}))
    monkeypatch.setattr(fix, 'run', lambda args: 'usr/lib/modules/kernel/uvcvideo.ko.zst')
    with pytest.raises(RuntimeError, match='Initrd embeds UVC'):
        fix.install(tmp_path, experimental=True)
    assert paths['CONFIG'].read_text() == BOOT
    assert not paths['STATE'].exists()
