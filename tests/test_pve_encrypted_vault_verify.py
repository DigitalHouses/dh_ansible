"""Offline PVE root-only encrypted Vault recovery acceptance; no real secrets."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest
import yaml
from ansible.parsing.vault import VaultLib, VaultSecret

ROOT = Path(__file__).resolve().parents[1]
PVE_VERIFY = ROOT / "bootstrap/import-pve-identities-vault.py"
HOST_INSTALL = ROOT / "bootstrap/semaphore-host-install.py"


def load(path):
    spec = importlib.util.spec_from_file_location(path.stem.replace("-", "_"), path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def prepared(monkeypatch, tmp_path, *, wrong_token=False):
    m = load(PVE_VERIFY)
    site = "demo-home"
    key = tmp_path / "pve_ed25519"
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)],
                   check=True, capture_output=True)
    monkeypatch.setattr(m, "PRIVATE_KEY", key)
    monkeypatch.setattr(m, "PUBLIC_KEY", Path(str(key) + ".pub"))
    monkeypatch.setattr(m.os, "geteuid", lambda: 0)
    original_checked = m.checked
    monkeypatch.setattr(
        m, "checked",
        lambda *argv: "ansible-" + site if argv == ("hostname",) else original_checked(*argv),
    )
    monkeypatch.setattr(
        m, "secure_read",
        lambda file, require_uid=0: Path(file).read_bytes()
        if (Path(file).stat().st_mode & 0o777) == 0o600
        else m.blocked("Unsafe synthetic permissions"),
    )
    stage_tokens = {
        "audit": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        "write": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
    }
    stage = {
        "identity_schema": m.PVE_SCHEMA,
        "site_id": site,
        "runner_ip": "192.0.2.231",
        "pve_ip": "192.0.2.30",
        "principals": dict(m.PVE_PRINCIPALS),
        "pve_tokens": stage_tokens,
    }
    staged = tmp_path / "escrow.json"
    staged.write_text(json.dumps(stage))
    staged.chmod(0o600)
    pve = {
        "identity_schema": m.PVE_SCHEMA,
        "principals": dict(m.PVE_PRINCIPALS),
        "audit_token_secret": stage_tokens["audit"],
        "write_token_secret": "incorrect-write-secret" if wrong_token else stage_tokens["write"],
        "host_write_ssh_private_key": key.read_text(),
    }
    secret_tree = {"fleet_secrets": {"sites": {site: {"pve": pve}}}}
    password = b"synthetic-master-password-only"
    ciphertext = VaultLib([("default", VaultSecret(password))]).encrypt(
        yaml.safe_dump(secret_tree).encode("utf-8"), secret=VaultSecret(password))
    vault = tmp_path / "vault.yml"
    vault.write_bytes(ciphertext)
    vault.chmod(0o600)
    master = tmp_path / "master"
    master.write_bytes(password + b"\n")
    master.chmod(0o600)
    args = SimpleNamespace(
        site=site, master_file=str(master), vault_file=str(vault),
        staged_tokens=str(staged), runner_ip=stage["runner_ip"], pve_ip=stage["pve_ip"]
    )
    return m, args, stage_tokens


def test_encrypted_fleet_matches_unchanged_original_pve_escrow(monkeypatch, tmp_path, capsys):
    m, args, tokens = prepared(monkeypatch, tmp_path)
    m.verify_worker(args)
    output = capsys.readouterr().out
    assert "PVE_ENCRYPTED_VAULT_ESCROW_MATCH=PASS" in output
    assert "PVE_VAULT_SECRET_VALUES_PRINTED=NO" in output
    assert all(secret not in output for secret in tokens.values())


def test_verify_blocks_mismatched_encrypted_write_token(monkeypatch, tmp_path):
    m, args, _ = prepared(monkeypatch, tmp_path, wrong_token=True)
    with pytest.raises(RuntimeError, match="Encrypted Fleet API tokens differ"):
        m.verify_worker(args)


def test_verification_mode_requires_pins_and_prohibits_apply(monkeypatch):
    mod = load(HOST_INSTALL)
    seen = []
    monkeypatch.setattr(mod, "commission_pve_preflight", lambda args: seen.append("read"))
    monkeypatch.setattr(mod, "commission_existing_pve_access",
                        lambda args, **kwargs: seen.append(("verify", kwargs)))
    argv = ["--site", "demo-home", "--runner-cidr", "192.0.2.231/24",
            "--gateway", "192.0.2.1", "--pve-ip", "192.0.2.30",
            "--expected-commit", "a" * 40, "--expected-fleet-commit", "b" * 40,
            "--verify-pve-access"]
    assert mod.main(argv) == 0
    assert seen == ["read", ("verify", {"verify_only": True})]
    with pytest.raises(mod.Blocked, match="cannot be combined with changes"):
        mod.main(argv + ["--apply"])
