#!/usr/bin/env python3
"""Install/restore this user's quiet desktop and login launchers."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def desktop_argument(path):
    text = str(path)
    for char in ('\\', '"', '`', '$'):
        text = text.replace(char, '\\' + char)
    return '"' + text + '"'


def configure(home, root=ROOT, *, restore=False):
    home, root = Path(home), Path(root)
    targets = {'desktop': home / 'Desktop/HDF_Vision.desktop',
               'autostart': home / '.config/autostart/HDF_Vision.desktop'}
    backup = home / '.local/state/hdf-vision/startup-launchers/original'
    manifest = backup / 'manifest.json'
    if restore:
        if not manifest.exists():
            raise RuntimeError('Záloha pôvodného spúšťania neexistuje.')
        original = json.loads(manifest.read_text())
        for key, path in targets.items():
            if original[key]:
                shutil.copy2(backup / (key + '.desktop'), path)
            else:
                path.unlink(missing_ok=True)
        shutil.rmtree(backup)
        return
    if not manifest.exists():
        backup.mkdir(parents=True, exist_ok=True)
        original = {}
        for key, path in targets.items():
            original[key] = path.exists()
            if path.exists():
                shutil.copy2(path, backup / (key + '.desktop'))
        manifest.write_text(json.dumps(original) + '\n')
    text = ('[Desktop Entry]\nVersion=1.0\nType=Application\nName=HDF Vision\n'
            'Comment=Run HDF Vision QC\n'
            f'Exec=/usr/bin/python3 {desktop_argument(root / "tools/launch-app.py")}\n'
            f'Path={root}\nIcon={root / "app/assets/hdf_vision.png"}\n'
            'Terminal=false\nStartupNotify=false\nX-GNOME-Autostart-enabled=true\n')
    for key, path in targets.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        content = text + ('X-GNOME-Autostart-Phase=Initialization\n' if key == 'autostart' else '')
        with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, delete=False) as stream:
            stream.write(content)
            temporary = Path(stream.name)
        temporary.chmod(0o755)
        temporary.replace(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--restore', action='store_true')
    args = parser.parse_args()
    if os.geteuid() == 0:
        parser.error('Spustite ako používateľ pracovnej plochy, bez sudo.')
    configure(Path.home(), restore=args.restore)
    if not args.restore and shutil.which('gio'):
        subprocess.run(['gio', 'set', str(Path.home() / 'Desktop/HDF_Vision.desktop'),
                        'metadata::trusted', 'true'], check=False)
    print('Pôvodné spúšťanie obnovené.' if args.restore else 'Spúšťanie s logom a bez terminálu je nastavené.')


if __name__ == '__main__':
    main()
