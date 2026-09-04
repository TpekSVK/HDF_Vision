"""Recipe-write password configuration and runtime ADMIN policy."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets


class SecurityService:
    VERSION = 1
    ALGORITHM = "pbkdf2_sha256"
    ITERATIONS = 300_000

    def __init__(self, path: str | Path = "/data/security.json") -> None:
        self.path = Path(path)

    def _read(self) -> dict | None:
        try:
            # Repair overly broad permissions even when the payload is malformed.
            os.chmod(self.path, 0o600)
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return None
            if data.get("version") != self.VERSION or data.get("algorithm") != self.ALGORITHM:
                return None
            iterations = data.get("iterations")
            if not isinstance(iterations, int) or iterations < self.ITERATIONS:
                return None
            salt = base64.b64decode(data["salt"], validate=True)
            digest = base64.b64decode(data["password_hash"], validate=True)
            if len(salt) < 16 or len(digest) != 32:
                return None
            return {**data, "_salt": salt, "_digest": digest}
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            return None

    def has_password(self) -> bool:
        return self._read() is not None

    def set_password(self, password: str) -> None:
        if not isinstance(password, str) or not password:
            raise ValueError("Password must not be empty")
        salt = secrets.token_bytes(32)
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt, self.ITERATIONS
        )
        config = {
            "version": self.VERSION,
            "algorithm": self.ALGORITHM,
            "iterations": self.ITERATIONS,
            "salt": base64.b64encode(salt).decode("ascii"),
            "password_hash": base64.b64encode(digest).decode("ascii"),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.{secrets.token_hex(8)}.tmp")
        try:
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(config, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            os.chmod(self.path, 0o600)
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

    def verify_password(self, password: str) -> bool:
        config = self._read()
        if config is None or not isinstance(password, str):
            return False
        candidate = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), config["_salt"], config["iterations"]
        )
        return hmac.compare_digest(candidate, config["_digest"])

    def change_password(self, old_password: str, new_password: str) -> bool:
        if not new_password or not self.verify_password(old_password):
            return False
        self.set_password(new_password)
        return True

    def remove_password(self, password: str) -> bool:
        if not self.verify_password(password):
            return False
        try:
            self.path.unlink()
        except FileNotFoundError:
            return False
        return True

    def is_admin_mode(self) -> bool:
        return os.environ.get("HDF_ADMIN_MODE", "0") == "1"

    def requires_password(self) -> bool:
        return self.has_password() and not self.is_admin_mode()

    def authorization_source(self) -> str:
        if self.is_admin_mode():
            return "admin"
        return "password" if self.has_password() else "unprotected"
