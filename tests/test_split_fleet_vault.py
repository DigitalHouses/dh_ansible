"""Safe, synthetic-only contracts for site Passport Vault partition."""
from pathlib import Path
import importlib.util
import os

import pytest
import yaml

SRC = Path(__file__).resolve().parents[1] / "bootstrap/split-fleet-vault.py"


def load():
    spec = importlib.util.spec_from_file_location("split_fleet_vault", SRC)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def example():
    return {
        "fleet_secrets": {
            "sites": {
                "pilot": {"pve": {"audit_token_secret": "synthetic-audit"}},
                "second": {"pve": {"audit_token_secret": "synthetic-other"}},
            },
            "shared": {"truenas_human_admin_password": "synthetic-shared"},
        }
    }


def test_audit_recognizes_schema_but_never_needs_real_fleet_vault():
    m = load()
    sites, shared, flat = m.parse_legacy(yaml.safe_dump(example()).encode())
    assert set(sites) == {"pilot", "second"}
    assert len(shared) == 1
    assert not flat
    for illegal in ({"fleet_secrets": {"sites": {"pilot": {}}, "shared": {"unknown": "value"}}},
                    {"fleet_secrets": {"sites": {"pilot": {}}, "other": {}}}):
        with pytest.raises(m.Blocked):
            m.parse_legacy(yaml.safe_dump(illegal).encode())


def test_mapping_requires_full_bijection_and_owner_type_slug():
    m = load()
    sites = example()["fleet_secrets"]["sites"]
    expected = {"pilot": "demo-office", "second": "other-home"}
    assert m.parse_mapping(["pilot=demo-office", "second=other-home"], sites) == expected
    for invalid in (["pilot=demo-office"],
                    ["pilot=demo-office", "second=demo-office"],
                    ["pilot=demo_office", "second=other-home"],
                    ["pilot=demo-office", "second=other-home", "unknown=new-home"]):
        with pytest.raises(m.Blocked):
            m.parse_mapping(invalid, sites)


def test_explicit_shared_password_distribution_and_site_isolation():
    m = load()
    sites, shared, flat = m.parse_legacy(yaml.safe_dump(example()).encode())
    mapping = m.parse_mapping(["pilot=demo-office", "second=other-home"], sites)
    with pytest.raises(m.Blocked, match="SHARED_REQUIRES"):
        m.partition(sites, shared, flat, mapping)
    result = m.partition(sites, shared, flat, mapping, duplicate_shared=True)
    assert result["demo-office"]["site_passport"]["site_id"] == "demo-office"
    assert result["demo-office"]["site_passport"]["site_secrets"] == sites["pilot"]
    assert result["other-home"]["site_passport"]["site_secrets"] == sites["second"]
    assert result["demo-office"]["site_passport"]["shared"] == shared
    result["demo-office"]["site_passport"]["site_secrets"]["pve"]["audit_token_secret"] = "changed"
    assert sites["pilot"]["pve"]["audit_token_secret"] == "synthetic-audit"


def test_legacy_flat_keys_require_owner_and_detect_conflict():
    m = load()
    sites = {"pilot": {"pve": {}}}
    flat = {"vault_pve_write_token_secret": "synthetic"}
    mapping = {"pilot": "demo-office"}
    with pytest.raises(m.Blocked, match="FLAT_PVE_REQUIRES"):
        m.partition(sites, {}, flat, mapping)
    assert m.partition(sites, {}, flat, mapping, flat_owner="pilot")["demo-office"]["site_passport"]["site_secrets"]["pve"]["write_token_secret"] == "synthetic"
    sites["pilot"]["pve"]["write_token_secret"] = "different"
    with pytest.raises(m.Blocked, match="CONFLICTING"):
        m.partition(sites, {}, flat, mapping, flat_owner="pilot")


def test_stage_encrypts_synthetic_vault_and_protects_permissions(tmp_path):
    m = load()
    from ansible.parsing.vault import VaultLib, VaultSecret
    secret = VaultSecret(b"synthetic-test-password")
    vault = VaultLib([("default", secret)])
    source = tmp_path / "legacy.vault.yml"
    encrypted = vault.encrypt(yaml.safe_dump(example()).encode(), secret=secret)
    source.write_bytes(encrypted)
    source.chmod(0o600)
    _, _, original, sites, shared, flat = m.audited_vault(source, secret.bytes)
    mapping = {"pilot": "demo-office", "second": "other-home"}
    output = tmp_path / "private"
    output.mkdir(mode=0o700)
    output.chmod(0o700)
    manifest = m.stage(output / "stage", vault, secret, original,
                       m.partition(sites, shared, flat, mapping, duplicate_shared=True))
    assert len(manifest["sites"]) == 2
    for site in manifest["sites"]:
        p = output / "stage" / "secrets" / site / "vault.yml"
        assert p.stat().st_mode & 0o777 == 0o600
        passport = yaml.safe_load(vault.decrypt(p.read_bytes()))
        assert passport["site_passport"]["site_id"] == site
    assert source.read_bytes() == encrypted
    with pytest.raises(m.Blocked, match="OUTPUT_ALREADY_EXISTS"):
        m.stage(output / "stage", vault, secret, original, {})


def test_source_rejects_unsafe_modes_or_plaintext(tmp_path):
    m = load()
    source = tmp_path / "input.yml"
    source.write_bytes(b"not a vault")
    source.chmod(0o600)
    with pytest.raises(m.Blocked, match="SOURCE_NOT_ENCRYPTED"):
        m.private_file(source)
    source.chmod(0o644)
    with pytest.raises(m.Blocked, match="SOURCE_OWNERSHIP"):
        m.private_file(source)
    source.unlink()
    source.symlink_to(tmp_path / "other")
    with pytest.raises(m.Blocked, match="SOURCE_SYMLINK"):
        m.private_file(source)


def test_no_git_or_subprocess_or_plaintext_staging():
    s = SRC.read_text()
    assert "subprocess" not in s
    assert "GitHub" in s
    assert "pct " not in s
    assert "git push" not in s
    assert "no_log" not in s
    assert "print(passport)" not in s
    assert "safe_dump(passport" in s
    assert "vault.encrypt(serialized" in s
