"""Offline safety contracts for the PVE root identity bootstrap."""
import importlib.util
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP = ROOT / "bootstrap" / "pve-identities.py"


def load():
    spec = importlib.util.spec_from_file_location("pve_identities", BOOTSTRAP)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_three_distinct_identity_contract():
    m = load()
    assert m.AUDIT_USER == "ansimble-audit@pve"
    assert m.WRITE_USER == "ansimble@pve"
    assert m.HOST_USER == "ansimble"
    text = BOOTSTRAP.read_text()
    assert '"PVEAuditor"' in text and '"PVEAdmin"' in text
    assert '"--privsep", "1"' in text
    assert '"--tokens"' in text and '"--users"' in text
    assert '"--apply"' in text
    assert "NO_PASSWORD" not in text


def test_existing_acl_exactness_and_fail_closed():
    m = load()
    ok = [{"type": "user", "ugid": "ansimble-audit@pve",
           "path": "/", "roleid": "PVEAuditor", "propagate": 1}]
    assert m.exact_acl(ok, "user", "ansimble-audit@pve", "PVEAuditor")
    assert not m.exact_acl([], "user", "ansimble-audit@pve", "PVEAuditor")
    import pytest
    with pytest.raises(RuntimeError, match="BLOCKED"):
        m.exact_acl([{**ok[0], "roleid": "Administrator"}],
                    "user", "ansimble-audit@pve", "PVEAuditor")
    with pytest.raises(RuntimeError, match="BLOCKED"):
        m.exact_acl([{**ok[0], "path": "/vms"}],
                    "user", "ansimble-audit@pve", "PVEAuditor")


def test_secret_staging_permissions_and_no_stdout(tmp_path, capsys):
    m = load()
    path = tmp_path / "pending.json"
    data = {"site_id": "home", "pve_tokens": {"audit": "example-sensitive-value"}}
    m.save_staging(path, data)
    assert json.loads(path.read_text()) == data
    assert path.stat().st_mode & 0o777 == 0o600
    assert "example-sensitive-value" not in capsys.readouterr().out


def test_host_ssh_transport_is_source_restricted():
    text = BOOTSTRAP.read_text()
    assert 'from="{source_ip}",restrict' in text
    assert "NOPASSWD: ALL" in text
    assert "pve_host_audit_known_hosts" in text
    assert "VAULT_PENDING" in text
    assert "regenerate automatically" in text
