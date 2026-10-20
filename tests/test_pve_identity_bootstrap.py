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
    assert m.AUDIT_USER == "ansible-audit@pve"
    assert m.WRITE_USER == "ansible-writer@pve"
    assert m.HOST_USER == "ansible-linux"
    text = BOOTSTRAP.read_text()
    assert '"PVEAuditor"' in text and '"PVEAdmin"' in text
    assert '"--privsep", "1"' in text
    assert '"--tokens"' in text and '"--users"' in text
    assert '"--apply"' in text
    assert "NO_PASSWORD" not in text


def test_existing_acl_exactness_and_fail_closed():
    m = load()
    ok = [{"type": "user", "ugid": "ansible-audit@pve",
           "path": "/", "roleid": "PVEAuditor", "propagate": 1}]
    assert m.exact_acl(ok, "user", "ansible-audit@pve", "PVEAuditor")
    assert not m.exact_acl([], "user", "ansible-audit@pve", "PVEAuditor")
    import pytest
    with pytest.raises(RuntimeError, match="BLOCKED"):
        m.exact_acl([{**ok[0], "roleid": "Administrator"}],
                    "user", "ansible-audit@pve", "PVEAuditor")
    with pytest.raises(RuntimeError, match="BLOCKED"):
        m.exact_acl([{**ok[0], "path": "/vms"}],
                    "user", "ansible-audit@pve", "PVEAuditor")


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
    assert 'runner_exec(ctid, "ssh-keygen"' in text
    assert 'runner_user_exec(ctid, "ssh-keygen"' not in text
    assert "ansible-v2-pending.json" in text
    assert "pve_host_audit_known_hosts" in text
    assert "VAULT_PENDING" in text
    assert "regenerate automatically" in text

def test_root_bootstrap_uses_canonical_runner_and_separate_v2_escrow():
    source = BOOTSTRAP.read_text()
    assert "ansible-{args.site}" in source
    assert "ansimble-{args.site}" not in source
    assert "ansible-v2-pending.json" in source
    assert '"identity_schema": 2' in source
    assert '"principals": {"audit": AUDIT_USER' in source
    assert '"/var/lib/ansible-linux"' in source
    assert '90-digitalhouses-ansible-linux' in source
    assert '"600:0:0"' in source


def test_installer_stages_identity_from_pinned_code_under_pve_root():
    source = (ROOT / "bootstrap" / "semaphore-host-install.py").read_text()
    assert '"bootstrap/pve-identities.py"' in source
    assert "def provision_pve_identity_stage(" in source
    assert 'run("python3", str(source),' in source
    assert "PVE_CREDENTIALS=ENCRYPTED_FLEET_PUBLISHED" in source
    assert "PVE_CREDENTIALS=RESTORED_FROM_FLEET" in source
    assert source.index("stage_runner_remote_commander(args.site, checkout)") < source.index(
        "provision_pve_identity_stage(args.site, args.runner_cidr,"
    )
