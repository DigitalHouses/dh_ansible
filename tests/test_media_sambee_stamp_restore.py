"""Sambee recovery requires a verified live firstboot login, never a stamp alone."""
import hashlib
import importlib.util
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]

def load_module():
    src = ROOT / "roles/media_guest_setup/files/dh-sambee-owner.py"
    spec = importlib.util.spec_from_file_location("sambee_rehydrate", src)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m

def stamp_fixture(tmp_path, monkeypatch):
    m = load_module()
    owner = tmp_path / "digitalhouses" / "credentials" / "owner.txt"
    owner.parent.mkdir(parents=True)
    monkeypatch.setattr(m, "credential", lambda _: ("owner", "Safe1234"))
    digest = hashlib.sha256(("owner" + chr(0) + "Safe1234").encode()).hexdigest()
    stamp = owner.parent / ".sambee-owner.sha256"
    stamp.write_text(digest + "\n")
    stamp.chmod(0o600)
    return m, owner, stamp

def test_recreate_restores_only_valid_initial_login(tmp_path, monkeypatch, capsys):
    m, path, stamp = stamp_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(m, "first_credentials", lambda _: ("owner", "temporary"))
    state = {"restored": False}
    def login(user, password):
        assert user == "owner"
        return "token" if (password == "temporary" or (state["restored"] and password == "Safe1234")) else None
    monkeypatch.setattr(m, "token", login)
    def change(endpoint, values, bearer=None):
        assert endpoint == "/api/auth/change-password"
        assert bearer == "token"
        assert values["current_password"] == "temporary"
        assert values["new_password"] == "Safe1234"
        state["restored"] = True
    monkeypatch.setattr(m, "request", change)
    m.reconcile(path, tmp_path / "compose.yml")
    assert "SAM_BEE_OWNER=RESTORED" in capsys.readouterr().out
    assert stamp.is_file() and stamp.stat().st_mode & 0o777 == 0o600
    m.reconcile(path, tmp_path / "compose.yml")
    assert "SAM_BEE_OWNER=READY" in capsys.readouterr().out

def test_mismatched_or_expired_bootstrap_fails_closed(tmp_path, monkeypatch):
    m, path, stamp = stamp_fixture(tmp_path, monkeypatch)
    stamp.write_text("0" * 64 + "\n")
    monkeypatch.setattr(m, "token", lambda *args: pytest.fail("token must not be tried"))
    with pytest.raises(RuntimeError, match="SAM_BEE_OWNER_ROTATION_NOT_COMMISSIONED"):
        m.reconcile(path, tmp_path / "compose.yml")
    digest = hashlib.sha256(("owner" + chr(0) + "Safe1234").encode()).hexdigest()
    stamp.write_text(digest + "\n")
    monkeypatch.setattr(m, "token", lambda *args: None)
    monkeypatch.setattr(m, "first_credentials", lambda _: ("owner", "expired"))
    monkeypatch.setattr(m, "request", lambda *args, **kwargs: pytest.fail("must not change password"))
    with pytest.raises(RuntimeError, match="SAM_BEE_OWNER_CREDENTIAL_DRIFT"):
        m.reconcile(path, tmp_path / "compose.yml")

def test_ansible_marks_recovered_sambee_changed():
    role = (ROOT / "roles/media_guest_setup/tasks/apps.yml").read_text()
    assert "SAM_BEE_OWNER=RESTORED" in role
