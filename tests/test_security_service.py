import json
import stat

from app.services.security_service import SecurityService


def test_password_lifecycle_and_permissions(tmp_path):
    path = tmp_path / "security.json"
    service = SecurityService(path)
    assert not service.has_password()
    service.set_password("secret")
    assert path.exists()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert service.verify_password("secret")
    assert not service.verify_password("wrong")
    assert not service.change_password("wrong", "new")
    assert service.change_password("secret", "new")
    assert not service.verify_password("secret")
    assert service.verify_password("new")
    assert not service.remove_password("wrong")
    assert path.exists()
    assert service.remove_password("new")
    assert not path.exists()


def test_random_salts_and_corruption(tmp_path):
    first = SecurityService(tmp_path / "one.json")
    second = SecurityService(tmp_path / "two.json")
    first.set_password("same")
    second.set_password("same")
    a = json.loads(first.path.read_text())
    b = json.loads(second.path.read_text())
    assert a["salt"] != b["salt"]
    assert a["password_hash"] != b["password_hash"]
    first.path.write_text("not-json")
    assert not first.has_password()
    assert not first.verify_password("same")


def test_admin_and_requires_password(tmp_path, monkeypatch):
    service = SecurityService(tmp_path / "security.json")
    monkeypatch.delenv("HDF_ADMIN_MODE", raising=False)
    assert not service.is_admin_mode()
    assert not service.requires_password()
    service.set_password("secret")
    assert service.requires_password()
    monkeypatch.setenv("HDF_ADMIN_MODE", "0")
    assert not service.is_admin_mode()
    monkeypatch.setenv("HDF_ADMIN_MODE", "1")
    assert service.is_admin_mode()
    assert not service.requires_password()
