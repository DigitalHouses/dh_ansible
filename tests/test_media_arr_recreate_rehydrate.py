"""Recreate+import Servarr Forms rehydration with immutable ZFS owner stamp."""
import hashlib
import importlib.util
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]

def get_module():
    path = ROOT / "roles/media_guest_setup/files/dh-media-arr-auth.py"
    spec = importlib.util.spec_from_file_location("arr_rehydrate_unit", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def test_recreated_sonarr_restores_only_pinned_owner(monkeypatch, tmp_path, capsys):
    app = get_module()
    path = tmp_path / "digitalhouses" / "credentials" / "serzhan.txt"
    path.parent.mkdir(parents=True)
    digest = hashlib.sha256(("serzhan" + chr(0) + "Safe1234").encode()).hexdigest()
    stamp = path.parent / ".arr-sonarr-owner.sha256"
    stamp.write_text(digest + "\n")
    stamp.chmod(0o600)
    state = {"id": 1, "authenticationMethod": "none",
             "authenticationRequired": "enabled", "username": ""}
    writes = []
    def mock_request(application, token, method="GET", values=None, rid=None):
        if method == "PUT":
            writes.append(values)
            state.update(values)
        return state.copy()
    monkeypatch.setattr(app, "api_key", lambda _: "dummy")
    monkeypatch.setattr(app, "request", mock_request)
    app.configure_one("sonarr", path, "serzhan", "Safe1234")
    assert "ARR_AUTH_SONARR=RESTORED" in capsys.readouterr().out
    assert len(writes) == 1 and writes[0]["username"] == "serzhan"
    assert stamp.read_text().strip() == digest
    app.configure_one("sonarr", path, "serzhan", "Safe1234")
    assert "ARR_AUTH_SONARR=UNCHANGED" in capsys.readouterr().out
    assert len(writes) == 1

def test_recreated_sonarr_rejects_foreign_login(monkeypatch, tmp_path):
    app = get_module()
    path = tmp_path / "digitalhouses" / "credentials" / "serzhan.txt"
    path.parent.mkdir(parents=True)
    digest = hashlib.sha256(("serzhan" + chr(0) + "Safe1234").encode()).hexdigest()
    stamp = path.parent / ".arr-sonarr-owner.sha256"
    stamp.write_text(digest + "\n")
    stamp.chmod(0o600)
    monkeypatch.setattr(app, "api_key", lambda _: "dummy")
    monkeypatch.setattr(app, "request", lambda *a, **kw: {"id":1,"authenticationMethod":"none",
                                                            "authenticationRequired":"enabled","username":"foreign"})
    with pytest.raises(RuntimeError, match="ARR_MANAGED_LOGIN_DRIFT"):
        app.configure_one("sonarr", path, "serzhan", "Safe1234")
    with pytest.raises(RuntimeError, match="ARR_OWNER_ROTATION_NOT_COMMISSIONED"):
        app.configure_one("sonarr", path, "serzhan", "Other123")

def test_ansible_tracks_rehydration():
    role = (ROOT / "roles/media_guest_setup/tasks/main.yml").read_text()
    assert "'=RESTORED' in media_arr_auth.stdout" in role
