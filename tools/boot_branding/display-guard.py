#!/usr/bin/env python3
"""One-boot recovery for the experimental display handoff, never a firmware update."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

STATE = Path('/var/lib/hdf-vision/boot-branding/state.json')
RESULT = Path('/var/lib/hdf-vision/boot-branding/display-guard-result.json')
ALLOWED = {
    '/etc/modules-load.d/hdf-boot-display.conf',
    '/etc/systemd/system/plymouth-start.service.d/hdf-display.conf',
    '/etc/systemd/system/gdm.service.d/zz-hdf-display-handoff.conf',
    '/usr/local/libexec/hdf-vision-display-handoff',
    '/usr/local/libexec/hdf-vision-display-guard',
    '/usr/share/backgrounds/hdf-vision.png',
    '/etc/X11/Xsession.d/00hdf-vision-background',
}


def stable_xserver():
    previous, stable = set(), 0
    for _ in range(12):
        result = subprocess.run(['pgrep', '-x', 'Xorg'], stdout=subprocess.PIPE, text=True, timeout=2)
        current = set(result.stdout.split())
        stable = stable + 1 if current and current == previous else 0
        if stable >= 5:
            return True
        previous = current
        time.sleep(1)
    return False


def command(*args, check=False):
    return subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          text=True, timeout=35, check=check)


def recover(metadata):
    owned = metadata.get('additional_created', [])
    if not owned or not set(owned).issubset(ALLOWED):
        raise RuntimeError('Unexpected recovery ownership; no system files removed')
    for name in owned:
        path = Path(name)
        if path.exists() and hashlib.sha256(path.read_bytes()).hexdigest() != metadata['destinations'][name]:
            raise RuntimeError('Display config changed by operator: ' + name)
    command('systemctl', 'stop', 'gdm.service', check=True)
    try:
        command('plymouth', 'quit')
        command('systemctl', 'stop', 'plymouth-start.service')
        for name in reversed(owned):
            Path(name).unlink(missing_ok=True)
            metadata['destinations'].pop(name, None)
        metadata.pop('additional_created', None)
        metadata.pop('display_handoff', None)
        metadata.pop('early_display_module', None)
        metadata.pop('x_background', None)
        temporary = STATE.with_suffix('.tmp')
        temporary.write_text(json.dumps(metadata, indent=2) + '\n')
        temporary.chmod(0o644)
        temporary.replace(STATE)
        command('systemctl', 'daemon-reload', check=True)
        command('modprobe', '-r', 'nvidia_drm')
    finally:
        command('systemctl', 'start', 'gdm.service', check=True)


def main():
    if os.geteuid() != 0:
        raise RuntimeError('Recovery guard requires root')
    if not STATE.exists():
        return
    metadata = json.loads(STATE.read_text())
    if not metadata.get('display_handoff'):
        return
    result = {'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip()}
    try:
        if stable_xserver():
            result['status'] = 'stable_xserver'
        else:
            recover(metadata)
            result['status'] = 'experimental_display_removed_gdm_restarted'
    except Exception as exc:
        result.update(status='recovery_error', error=str(exc))
        raise
    finally:
        RESULT.write_text(json.dumps(result, indent=2) + '\n')


if __name__ == '__main__':
    main()
