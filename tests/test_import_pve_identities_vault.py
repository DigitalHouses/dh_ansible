"""Non-production contract for importing PVE identities into Fleet Vault."""
from pathlib import Path
import importlib.util
import pytest
import tempfile
import os
ROOT=Path(__file__).resolve().parents[1]
SCRIPT=ROOT/"bootstrap/import-pve-identities-vault.py"
def load():
    spec=importlib.util.spec_from_file_location("pve_vault_import", SCRIPT)
    mod=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod
def test_vault_import_uses_in_memory_crypto_not_plaintext_git():
    src=SCRIPT.read_text()
    assert "VaultLib" in src and "VaultSecret" in src
    assert "vault.decrypt" in src and "vault.encrypt" in src
    assert "os.O_EXCL" in src
    assert "VAULT_CANDIDATE=PASS" in src
    assert 'secure_read(Path(args.master_file))' in src
    assert "PVE_STAGING=RETAINED_UNTIL_GIT_VERIFY" in src
    assert "git push" not in src
def test_host_worker_no_secret_in_cli_or_report():
    src=SCRIPT.read_text()
    assert 'guest_temp + "/master"' in src
    assert 'guest_temp + "/staging.json"' in src
    assert "SECRETS_IN_OUTPUT=NO" in src
    assert 'print(tokens)' not in src
    assert 'print(ssh_key)' not in src
def test_secure_read_rejects_loose_permissions(tmp_path):
    mod=load()
    f=tmp_path/"secret"
    f.write_text("fake-secret")
    os.chmod(f,0o644)
    with pytest.raises(RuntimeError,match="BLOCKED"):
        mod.secure_read(f,os.getuid())
    os.chmod(f,0o600)
    assert mod.secure_read(f,os.getuid())==b"fake-secret"


def test_host_worker_honors_current_runtime_and_historical_pilot():
    src = SCRIPT.read_text()
    assert 'parser.add_argument("--guest-python", default="/opt/ansible/venv/bin/python")' in src
    assert 'args.guest_python not in ("/opt/ansible/venv/bin/python",' in src
    assert '"/opt/ansimble/venv/bin/python")' in src
    assert 'pct(args.ctid, "test", "-x", args.guest_python)' in src
    assert 'print(pct(args.ctid, args.guest_python,' in src
    assert 'print(pct(args.ctid, "/opt/ansible/venv/bin/python",' not in src


def test_root_installer_forwards_discovered_interpreter_for_staged_import():
    src=(ROOT / "bootstrap/semaphore-host-install.py").read_text()
    assert '"--guest-python", guest_python, timeout=150' in src
    assert 'guest_python = commission_pve_preflight(args, checkout)' in src
    assert "guest_python=guest_python" in src
    assert "PVE_CANONICAL_IDENTITIES=ROOT_STAGE_PASS" in src
    assert "PVE_CREDENTIALS=ENCRYPTED_FLEET_PUBLISHED" in src
