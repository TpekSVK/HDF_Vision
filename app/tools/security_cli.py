"""Terminal-only recipe password management."""

from __future__ import annotations

import argparse
import getpass

from app.services.security_service import SecurityService


def _new_password(first: str = "Nové heslo: ", second: str = "Zopakovať heslo: ") -> str | None:
    password = getpass.getpass(first)
    confirmation = getpass.getpass(second)
    if not password:
        print("Heslo nesmie byť prázdne.")
        return None
    if password != confirmation:
        print("Heslá sa nezhodujú.")
        return None
    return password


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bash docker/security.sh", description="Správa ochrany receptov")
    parser.add_argument("command", choices=("set-password", "change-password", "verify", "status", "remove-password", "reset-password", "help"), nargs="?")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command in (None, "help"):
        parser.print_help()
        return 0
    service = SecurityService()
    if args.command == "status":
        print(f"Ochrana receptov: {'ZAPNUTÁ' if service.has_password() else 'VYPNUTÁ'}")
        print("Súbor: /data/security.json")
        print("Algoritmus: PBKDF2-SHA256")
        return 0
    if args.command == "set-password":
        if service.has_password():
            print("Heslo už existuje. Použite change-password.")
            return 1
        password = _new_password()
        if password is None:
            return 1
        service.set_password(password)
        print("Heslo bolo úspešne nastavené.")
        return 0
    if args.command == "change-password":
        if not service.has_password():
            print("Ochrana receptov nie je nastavená.")
            return 1
        old = getpass.getpass("Aktuálne heslo: ")
        if not service.verify_password(old):
            print("Nesprávne heslo.")
            return 1
        password = _new_password("Nové heslo: ", "Zopakovať nové heslo: ")
        if password is None:
            return 1
        service.set_password(password)
        print("Heslo bolo úspešne zmenené.")
        return 0
    if args.command == "verify":
        valid = service.verify_password(getpass.getpass("Heslo: "))
        print("Heslo je správne." if valid else "Nesprávne heslo.")
        return 0 if valid else 1
    if args.command == "remove-password":
        if not service.has_password():
            print("Ochrana receptov nie je nastavená.")
            return 1
        password = getpass.getpass("Aktuálne heslo: ")
        if not service.verify_password(password):
            print("Nesprávne heslo.")
            return 1
        if input("Naozaj chcete vypnúť ochranu receptov? [y/N]: ") not in ("y", "Y"):
            print("Operácia zrušená.")
            return 1
        service.remove_password(password)
        print("Ochrana receptov bola vypnutá.")
        return 0
    if args.command == "reset-password":
        print("ADMIN RESET OCHRANY RECEPTOV")
        password = _new_password("Nové heslo: ", "Zopakovať nové heslo: ")
        if password is None:
            return 1
        service.set_password(password)
        print("Heslo bolo úspešne resetované.")
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
