"""Boot edits must preserve the USB kernel, DT overlays and a usable fallback."""
import importlib.util
from pathlib import Path
import pytest

spec = importlib.util.spec_from_file_location('boot_branding', Path(__file__).parents[1] / 'tools/setup-boot-branding.py')
boot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(boot)

CONFIG = '''TIMEOUT 30
DEFAULT USB_FIX
LABEL stock
 MENU LABEL original
 LINUX /boot/Image
 INITRD /boot/initrd
 APPEND root=/dev/nvme0n1p1 console=tty0
LABEL USB_FIX
 MENU LABEL camera kernel
 LINUX /boot/Image.hdf-usb
 INITRD /boot/initrd
 FDT /boot/device.dtb
 APPEND ${cbootargs} root=/dev/nvme0n1p1 rw console=ttyTCU0,115200 console=tty0 video=efifb:off fbcon=map:0
 OVERLAYS /boot/gpio.dtbo
'''


def test_new_entry_preserves_original_entries_and_camera_boot_settings():
    updated = boot.branded_config(CONFIG)
    assert updated.split('LABEL HDF_VISION_BRANDED')[0].rstrip() == CONFIG.replace('DEFAULT USB_FIX', 'DEFAULT HDF_VISION_BRANDED').rstrip()
    new = updated.split('LABEL HDF_VISION_BRANDED')[1]
    for line in ('LINUX /boot/Image.hdf-usb', 'INITRD /boot/initrd', 'FDT /boot/device.dtb', 'OVERLAYS /boot/gpio.dtbo'):
        assert line in new
    assert '${cbootargs}' in new and 'root=/dev/nvme0n1p1' in new
    assert 'console=ttyTCU0,115200' in new and 'video=efifb:off' in new and 'fbcon=map:0' in new
    assert 'console=tty0' not in new and 'quiet splash' in new
    with pytest.raises(ValueError): boot.branded_config(updated)


@pytest.mark.parametrize('config', [CONFIG.replace('DEFAULT USB_FIX', 'DEFAULT missing'), CONFIG.replace(' APPEND ${cbootargs}', ' # APPEND ${cbootargs}'), CONFIG + '\nDEFAULT stock\n'])
def test_unknown_boot_layout_is_rejected(config):
    with pytest.raises(ValueError): boot.branded_config(config)


@pytest.fixture
def paths(tmp_path, monkeypatch):
    for name, file in [('EXTLINUX', 'boot/extlinux.conf'), ('PLYMOUTH', 'etc/plymouth.conf'),
                       ('THEME', 'themes/hdf'), ('STATE', 'state'), ('RELEASE', 'release'), ('BIOS', 'bios')]:
        monkeypatch.setattr(boot, name, tmp_path / file)
    boot.EXTLINUX.parent.mkdir(parents=True)
    boot.EXTLINUX.write_text(CONFIG)
    boot.PLYMOUTH.parent.mkdir(parents=True)
    boot.PLYMOUTH.write_text('[Daemon]\nTheme=bgrt\n')
    boot.RELEASE.write_text('R36.4.7'); boot.BIOS.write_text('36.4.7')
    monkeypatch.setattr(boot.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(boot.os, 'sync', lambda: None)
    monkeypatch.setattr(boot, 'EARLY_MODULES', tmp_path / 'modules-load.d/hdf.conf')
    monkeypatch.setattr(boot, 'PLYMOUTH_DROPIN', tmp_path / 'systemd/plymouth-start.service.d/hdf.conf')
    monkeypatch.setattr(boot.subprocess, 'check_output', lambda *a, **kw: '/lib/modules/nvidia-drm.ko\n')
    monkeypatch.setattr(boot.subprocess, 'run', lambda *a, **kw: None)
    stage = tmp_path / 'staged'
    boot.stage(stage)
    return stage


def test_install_and_restore_keep_original_configuration(paths):
    original_plymouth = boot.PLYMOUTH.read_bytes()
    boot.install(paths)
    assert 'DEFAULT HDF_VISION_BRANDED' in boot.EXTLINUX.read_text()
    assert boot.THEME.joinpath('brand.png').exists()
    boot.restore()
    assert boot.EXTLINUX.read_text() == CONFIG
    assert boot.PLYMOUTH.read_bytes() == original_plymouth
    assert not boot.THEME.exists() and not boot.STATE.exists()


@pytest.mark.parametrize('changed', ['boot', 'payload', 'bios'])
def test_changes_after_staging_block_install(paths, changed):
    if changed == 'boot': boot.EXTLINUX.write_text(CONFIG + '# changed\n')
    if changed == 'payload': (paths / 'extlinux.conf').write_text('invalid')
    if changed == 'bios': boot.BIOS.write_text('36.5.0')
    before = boot.EXTLINUX.read_bytes()
    with pytest.raises(RuntimeError): boot.install(paths)
    assert boot.EXTLINUX.read_bytes() == before and not boot.STATE.exists()


def test_rollback_refuses_to_overwrite_later_manual_changes(paths):
    boot.install(paths)
    boot.EXTLINUX.write_text(boot.EXTLINUX.read_text() + '# manual change\n')
    with pytest.raises(RuntimeError, match='dodatočne'): boot.restore()
    assert boot.STATE.exists()


def test_partial_installation_never_changes_default_and_can_restore(paths, monkeypatch):
    original_plymouth = boot.PLYMOUTH.read_bytes()
    write = boot.write_atomic
    def failing_write(path, content):
        if path == boot.EXTLINUX: raise OSError('simulated disk write failure')
        write(path, content)
    monkeypatch.setattr(boot, 'write_atomic', failing_write)
    with pytest.raises(OSError): boot.install(paths)
    assert boot.EXTLINUX.read_text() == CONFIG
    monkeypatch.setattr(boot, 'write_atomic', write)
    boot.restore()
    assert boot.EXTLINUX.read_text() == CONFIG
    assert boot.PLYMOUTH.read_bytes() == original_plymouth


def test_early_display_is_idempotent_and_full_restore_removes_owned_configs(paths):
    boot.install(paths)
    installed_config = boot.EXTLINUX.read_bytes()
    boot.early_display()
    boot.early_display()
    assert 'nvidia_drm' in boot.EARLY_MODULES.read_text()
    assert 'After=systemd-modules-load.service' in boot.PLYMOUTH_DROPIN.read_text()
    assert boot.EXTLINUX.read_bytes() == installed_config
    boot.restore()
    assert not boot.EARLY_MODULES.exists() and not boot.PLYMOUTH_DROPIN.exists()
    assert boot.EXTLINUX.read_text() == CONFIG


def test_early_display_refuses_existing_unowned_config(paths):
    boot.install(paths)
    boot.EARLY_MODULES.parent.mkdir(parents=True)
    boot.EARLY_MODULES.write_text('operator setting\n')
    with pytest.raises(RuntimeError, match='už existuje'): boot.early_display()
    assert boot.EARLY_MODULES.read_text() == 'operator setting\n'


def test_partial_early_display_can_resume_and_restore(paths, monkeypatch):
    boot.install(paths)
    write = boot.write_atomic
    def fail(path, content):
        if path == boot.PLYMOUTH_DROPIN: raise OSError('disk failure')
        write(path, content)
    monkeypatch.setattr(boot, 'write_atomic', fail)
    with pytest.raises(OSError): boot.early_display()
    assert boot.EARLY_MODULES.exists() and not boot.PLYMOUTH_DROPIN.exists()
    monkeypatch.setattr(boot, 'write_atomic', write)
    boot.early_display()
    boot.restore()
    assert not boot.EARLY_MODULES.exists() and not boot.PLYMOUTH_DROPIN.exists()
