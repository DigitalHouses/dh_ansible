"""Offline contract tests for canonical PVE identity recovery (no infrastructure)."""
import importlib.util
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest
import yaml
from ansible.parsing.vault import VaultLib, VaultSecret

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "bootstrap/import-pve-identities-vault.py"
SITE = "demo-home"


def load():
    spec = importlib.util.spec_from_file_location("pve_recovery", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def encrypted(path, pve=None):
    password = b"synthetic-test-master-password"
    source = {"fleet_secrets": {"sites": {SITE: {"pve": pve or {}}}}}
    ciphertext = VaultLib([("default", VaultSecret(password))]).encrypt(
        yaml.safe_dump(source).encode(), secret=VaultSecret(password))
    path.write_bytes(ciphertext)
    path.chmod(0o600)
    return password


def prepare(monkeypatch, tmp_path):
    m = load()
    m.PRIVATE_KEY = tmp_path / "ssh" / "pve_host_write_ed25519"
    m.PUBLIC_KEY = tmp_path / "ssh" / "pve_host_write_ed25519.pub"
    monkeypatch.setattr(m.os, "geteuid", lambda: 0)
    real_run = m.checked
    def checked(*argv):
        if argv == ("hostname",):
            return "ansible-" + SITE
        return real_run(*argv)
    monkeypatch.setattr(m, "checked", checked)
    def secure_read(path, require_uid=0):
        assert path.is_file()
        assert (path.stat().st_mode & 0o777) == 0o600
        return path.read_bytes()
    monkeypatch.setattr(m, "secure_read", secure_read)
    source = tmp_path / "vault.yml"
    master_file = tmp_path / "master"
    args = SimpleNamespace(site=SITE, vault_file=str(source), master_file=str(master_file))
    return m, args, source, master_file


def test_absent_canonical_credentials_does_not_generate_keys(monkeypatch, tmp_path, capsys):
    m, args, source, master = prepare(monkeypatch, tmp_path)
    master.write_bytes(encrypted(source) + b"\n")
    master.chmod(0o600)
    m.recover_worker(args)
    assert "PVE_FLEET_RECOVERY=ABSENT" in capsys.readouterr().out
    assert not m.PRIVATE_KEY.exists()


def test_partial_canonical_secrets_fail_closed(monkeypatch, tmp_path):
    m, args, source, master = prepare(monkeypatch, tmp_path)
    master.write_bytes(encrypted(source, {"audit_token_secret": "a" * 36}) + b"\n")
    master.chmod(0o600)
    with pytest.raises(RuntimeError, match="Unversioned or conflicting"):
        m.recover_worker(args)


def test_recovers_exact_original_key_and_is_idempotent(monkeypatch, tmp_path, capsys):
    m, args, source, master = prepare(monkeypatch, tmp_path)
    original = tmp_path / "original"
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(original)],
                   check=True)
    ssh_bytes = original.read_text()
    tokens = {
        "identity_schema": 2,
        "principals": {"audit": "ansible-audit@pve", "write": "ansible-writer@pve", "linux": "ansible-linux"},
        "audit_token_secret": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        "write_token_secret": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
        "host_write_ssh_private_key": ssh_bytes,
    }
    master.write_bytes(encrypted(source, tokens) + b"\n")
    master.chmod(0o600)
    m.recover_worker(args)
    assert m.PRIVATE_KEY.read_text() == ssh_bytes
    assert (m.PRIVATE_KEY.stat().st_mode & 0o777) == 0o600
    assert m.PUBLIC_KEY.exists()
    m.recover_worker(args)
    out = capsys.readouterr().out
    assert out.count("PVE_FLEET_RECOVERY=PASS") == 2
    assert tokens["audit_token_secret"] not in out
    assert tokens["write_token_secret"] not in out

def test_recovery_never_adopts_legacy_unversioned_pve_secrets(monkeypatch, tmp_path):
    m, args, source, master = prepare(monkeypatch, tmp_path)
    old = {
        "audit_token_secret": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        "write_token_secret": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
        "host_write_ssh_private_key": "historical-key-must-not-be-adopted",
    }
    master.write_bytes(encrypted(source, old) + b"\n")
    master.chmod(0o600)
    with pytest.raises(RuntimeError, match="Unversioned or conflicting"):
        m.recover_worker(args)
    assert not m.PRIVATE_KEY.exists()


def test_recovery_refuses_wrong_canonical_principals(monkeypatch, tmp_path):
    m, args, source, master = prepare(monkeypatch, tmp_path)
    old = {
        "identity_schema": 2,
        "principals": {"audit": "somebody-else@pve", "write": "ansible-writer@pve", "linux": "ansible-linux"},
        "audit_token_secret": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        "write_token_secret": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
        "host_write_ssh_private_key": "historical-key-must-not-be-adopted",
    }
    master.write_bytes(encrypted(source, old) + b"\n")
    master.chmod(0o600)
    with pytest.raises(RuntimeError, match="Unversioned or conflicting"):
        m.recover_worker(args)
    assert not m.PRIVATE_KEY.exists()
