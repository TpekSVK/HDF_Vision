"""Interactive local contact configuration, independent of Qt and Git."""
import argparse
import json
import os
from pathlib import Path
import tempfile


def main(argv=None, *, contact_path=Path('/data/contact.json')):
    parser = argparse.ArgumentParser(prog='bash docker/contact.sh')
    parser.add_argument('command', choices=('set-info',))
    parser.parse_args(argv)
    temporary = None
    try:
        contact = {key: input(prompt).strip() for key, prompt in (
            ('name', 'Meno: '), ('phone', 'Telefón: '), ('email', 'E-mail: '))}
        if not all(contact.values()):
            print('Všetky údaje musia byť vyplnené. Kontakt nebol zmenený.')
            return 1
        path = Path(contact_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        # A private temporary file and atomic replacement preserve the previous
        # contact if writing fails or the operation is interrupted.
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8',
                dir=path.parent, prefix='.contact-', delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(contact, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        print('Kontakt bol uložený. Otvorte Kontakt v Troubleshooting.')
        return 0
    except (EOFError, KeyboardInterrupt):
        print('\nOperácia zrušená. Kontakt nebol zmenený.')
        return 1
    except OSError:
        print('Kontakt sa nepodarilo uložiť. Skontrolujte prístup k dátovému adresáru.')
        return 1
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


if __name__ == '__main__':
    raise SystemExit(main())
