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

def test_restored_ed25519_public_key_regex_accepts_comments():
    import ast
    import re
    tree = ast.parse(BOOTSTRAP.read_text())
    expressions = (
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "fullmatch"
        and len(node.args) >= 2
        and isinstance(node.args[1], ast.Name)
        and node.args[1].id == "restored_pub"
    )
    expr = next(expressions)
    pattern = ast.literal_eval(expr.args[0])
    assert re.fullmatch(pattern, "ssh-ed25519 " + "A" * 44 + " digitalhouses-demo-home-pve-host")
    assert not re.fullmatch(pattern, "ssh-ed25519 " + "A" * 44 + "\\nINJECTED")


def test_existing_canonical_staging_never_reissues_pve_tokens(monkeypatch, tmp_path, capsys):
    mod_spec = importlib.util.spec_from_file_location(
        "pve_commission", ROOT / "bootstrap/semaphore-host-install.py")
    mod = importlib.util.module_from_spec(mod_spec)
    mod_spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "ROOT_STAGE", tmp_path)
    path = tmp_path / "pve-identities-demo-home-ansible-v2-pending.json"
    path.write_text(json.dumps({
        "site_id": "demo-home",
        "identity_schema": 2,
        "runner_ip": "192.0.2.231",
        "pve_ip": "192.0.2.30",
        "principals": {
            "audit": "ansible-audit@pve",
            "write": "ansible-writer@pve",
            "linux": "ansible-linux",
        },
        "pve_tokens": {
            "audit": "a" * 36,
            "write": "b" * 36,
        },
    }))
    path.chmod(0o600)
    calls = []
    def verified_read_only(*args, **kwargs):
        calls.append(args)
        return "PVE_RESTORED_IDENTITIES=READ_ONLY_PASS"
    monkeypatch.setattr(mod, "run", verified_read_only)
    assert mod.pve_staged_enrollment_verified(
        "demo-home", "192.0.2.231", "192.0.2.30", tmp_path / "bootstrap.py")
    assert len(calls) == 1
    assert "--reconcile-restored" in calls[0]
    assert "--apply" not in calls[0]
    assert "STAGING_VERIFIED_NO_REISSUE" in capsys.readouterr().out
