#!/usr/bin/env python3
"""Install the complete tested Linux startup appearance, without rebooting."""
import argparse
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
STATE = Path('/var/lib/hdf-vision/boot-branding/state.json')
RELEASE = Path('/etc/nv_tegra_release')
BIOS = Path('/sys/class/dmi/id/bios_version')


def run(*args):
    print('+', ' '.join(map(str, args)), flush=True)
    subprocess.run([str(arg) for arg in args], check=True, cwd=ROOT)


def preflight():
    if os.geteuid() == 0:
        raise RuntimeError('Spustite ako používateľ pracovnej plochy, bez sudo.')
    if not RELEASE.exists() or not BIOS.exists():
        raise RuntimeError('Tento postup je určený pre overený Jetson Linux 36.4.7.')
    release = RELEASE.read_text()
    if not (re.search(r'R36\s+\(release\)', release) and
            re.search(r'REVISION:\s*4\.7\s*,', release) and
            re.match(r'^36\.4\.7(?:-|$)', BIOS.read_text().strip())):
        raise RuntimeError('Automatický postup je overený iba pre Jetson Linux a UEFI 36.4.7.')
    missing = [name for name in ('sudo', 'gsettings', 'plymouth', 'xsetbg', 'xsetroot', 'systemctl', 'modinfo')
               if not shutil.which(name)]
    if missing:
        raise RuntimeError('Chýbajú systémové nástroje: ' + ', '.join(missing))
    if not Path('/usr/lib/aarch64-linux-gnu/plymouth/script.so').is_file():
        raise RuntimeError('Chýba Plymouth script.so. Systém sa nemenil.')
    if os.environ.get('XDG_SESSION_TYPE', 'x11') != 'x11':
        raise RuntimeError('Spustite v termináli pracovnej plochy GNOME/X11.')
    run('modinfo', '-F', 'filename', 'nvidia_drm')
    run(sys.executable, '-c',
        'import gi; gi.require_version("Gtk", "3.0"); from gi.repository import Gtk, Gdk; '
        'assert Gdk.Display.get_default() is not None, "Nie je dostupná pracovná plocha X11."')


def install():
    # Validate tools and desktop access before any privileged or persistent changes.
    preflight()
    boot = ROOT / 'tools/setup-boot-branding.py'
    launcher = ROOT / 'tools/setup-launcher.py'
    run('sudo', '-v')
    if not STATE.exists():
        with tempfile.TemporaryDirectory(prefix='hdf-startup-') as temporary:
            staged = Path(temporary) / 'staged'
            run(sys.executable, boot, 'stage', '--output', staged)
            run('sudo', sys.executable, boot, 'install', '--staged', staged)
    # Existing installations are verified by the underlying installers before writes.
    run('sudo', sys.executable, boot, 'display-handoff', '--experimental')
    run('sudo', sys.executable, boot, 'x-background')
    run(sys.executable, launcher)
    run(sys.executable, launcher, '--background', 'hdf')
    print('Hotovo. Reštart nebol vykonaný. Zatvorte aplikáciu a reštartujte Jetson.')
    print('UEFI logo NVIDIA zostáva; jadro, initrd a firmvér sa nemenili.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='Overiť predpoklady bez inštalácie.')
    args = parser.parse_args()
    try:
        if args.check:
            preflight()
            print('Predpoklady splnené. Žiadne nastavenia sa nemenili.')
        else:
            install()
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f'Nastavenie sa zastavilo: {exc}\n')


if __name__ == '__main__':
    main()
