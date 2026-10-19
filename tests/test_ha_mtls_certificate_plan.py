"""Synthetic, no-network safety tests for fleet HA mTLS certificate planning."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "bin/ha-mtls-certificate-plan.py"


def module():
    spec = importlib.util.spec_from_file_location("ha_mtls_certificate_plan", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def synthetic_state(m):
    """All fixtures are fictional and valid for the private-data contract."""
    return {
        "site_id": "sample-home",
        "enabled": False, "live_writes_authorized": False,
        "hostname": "sample-home-ha-mtls.digitalhouses.vip",
        "cf_zone_name": "digitalhouses.vip",
        "cf_zone_id": "a" * 32, "cf_account_id": "b" * 32,
        "existing_tunnel_id": "11111111-1111-4111-8111-111111111111",
        "existing_tunnel_name": "Sample Tunnel",
        "existing_ha_hostname": "sample-home-ha.digitalhouses.vip",
        "existing_ha_origin": "http://203.0.113.21:8123",
        "cert_issue_strategy": "csr-generated-locally",
        "cert_subject": "CN-only-exact-hostname",
        "cert_key_storage": "encrypted-fleet-vault",
        "certificate_policy": "one-per-home-shared-with-residents",
        "trust_scope": "Cloudflare-managed-CA",
        "cert_validity_days": 365,
        "p12_destination": "/mnt/sample_pool/digitalhouses/_cert",
        "p12_bundle": {
            "layout": "dual-pkcs12-with-password-sidecar",
            "output_directory_mode": "0700", "output_file_mode": "0600",
            "filename_scheme": "host-prefix-and-generation8",
            "publish_policy": "no-overwrite-idempotent",
            "filebrowser_uid": 3200, "filebrowser_gid": 3102,
        },
        "certificate_recovery": {
            "vault_sha256": "a" * 64, "certificate_sha256": "b" * 64,
            "generation_id": "c" * 32,
            "certificate_id": "11111111-1111-4111-8111-111111111111",
            "published_p12_size_bytes": 3007,
        },
        "waf": {"apply_authorized": False, "rule_name": m.RULE_NAME,
                "target_pattern": "*-ha-mtls.digitalhouses.vip"},
        "legacy_guard": {
            "waf_hostname": "pilot-ha-mtls-001.digitalhouses.vip",
            "ca_hostname": "pilot-ha-mtls-001.digitalhouses.vip",
        },
    }


def synthetic_inventory(m, root):
    site_dir = root / "inventories" / "sample-home"
    site_dir.mkdir(parents=True)
    (site_dir / "cloudflare_ha_mtls.yml").write_text(
        yaml.safe_dump({"cloudflare_ha_mtls": synthetic_state(m)}),
        encoding="utf-8",
    )


def fake_api(m, state):
    zone = state["cf_zone_id"]
    account = state["cf_account_id"]
    tid = state["existing_tunnel_id"]
    return {
        f"/zones/{zone}": {
            "name": state["cf_zone_name"], "account": {"id": account},
            "plan": {"name": "Free Website"},
        },
        f"/accounts/{account}/cfd_tunnel/{tid}": {
            "name": state["existing_tunnel_name"], "config_src": "cloudflare",
        },
        f"/accounts/{account}/cfd_tunnel/{tid}/configurations": {
            "config": {"ingress": [
                {"hostname": state["existing_ha_hostname"],
                 "service": state["existing_ha_origin"]},
                {"service": "http_status:404"},
            ]}
        },
        f"/zones/{zone}/rulesets/phases/{m.PHASE}/entrypoint": {
            "rules": [{
                "action": "block",
                "enabled": True,
                "description": "Enforce mTLS authentication [Template]",
                "expression": (
                    '(http.host eq "' + state["legacy_guard"]["waf_hostname"] + '" and '
                    '(not cf.tls_client_auth.cert_verified or '
                    'cf.tls_client_auth.cert_revoked))'
                ),
            }]
        },
        f"/zones/{zone}/certificate_authorities/hostname_associations": {
            "hostnames": [state["legacy_guard"]["ca_hostname"]]
        },
        f"/zones/{zone}/client_certificates?status=all&per_page=50&page=1": [
            {"status": "active", "common_name": "Cloudflare"}
        ],
    }


class ReadOnlyStub:
    def __init__(self, responses):
        self.responses = responses
        self.paths = []

    def get(self, path):
        self.paths.append(path)
        return self.responses[path]


def test_intent_enforces_one_existing_vault_and_disabled_writes(tmp_path):
    m = module()
    synthetic_inventory(m, tmp_path)
    s = m.intent("sample-home", root=tmp_path)
    assert s["hostname"] == "sample-home-ha-mtls.digitalhouses.vip"
    assert s["cert_key_storage"] == "encrypted-fleet-vault"
    assert s["enabled"] is False
    assert s["live_writes_authorized"] is False
    assert s["waf"]["apply_authorized"] is False
    assert not any(x in str(s) for x in ("private_key:", "password:"))
    with pytest.raises(m.Block, match="INVALID_SITE_ID"):
        m.intent("../secrets", root=tmp_path)
    with pytest.raises(m.Block, match="SITE_INVENTORY_NOT_FOUND"):
        m.intent("missing-site", root=tmp_path)


def test_truenas_address_is_rfc1918_not_merely_special_use():
    m = module()
    for parts in (("10", "23", "45", "67"), ("172", "18", "0", "10"),
                  ("192", "168", "21", "42")):
        assert m.private_nas_ipv4(".".join(parts))
    for address in ("198.51.100.23", "203.0.113.50", "192.0.2.1",
                    "127.0.0.1", "169.254.20.1", "8.8.8.8"):
        assert not m.private_nas_ipv4(address)
    assert not m.private_nas_ipv4("not-an-ip")


def test_recovery_policy_is_private_and_validated(tmp_path):
    m = module()
    synthetic_inventory(m, tmp_path)
    cfg = m.accepted_issued_certificate("sample-home", root=tmp_path)
    assert cfg["hostname"] == "sample-home-ha-mtls.digitalhouses.vip"
    assert cfg["p12_filename"] == "sample-home-ha-mtls-cccccccc.p12"
    file = tmp_path / "inventories" / "sample-home" / "cloudflare_ha_mtls.yml"
    policy = yaml.safe_load(file.read_text(encoding="utf-8"))
    policy["cloudflare_ha_mtls"]["certificate_recovery"]["generation_id"] = "not-a-hash"
    file.write_text(yaml.safe_dump(policy), encoding="utf-8")
    with pytest.raises(m.Block, match="P12_RECOVERY_GENERATION_INVALID"):
        m.accepted_issued_certificate("sample-home", root=tmp_path)


def test_plan_is_readonly_and_preserves_pilot():
    m = module()
    state = synthetic_state(m)
    mock = ReadOnlyStub(fake_api(m, state))
    result = m.build_plan(state, mock)
    assert result["next_action"] == "ISSUE_AFTER_APPROVAL"
    assert result["active_certificates"] == 1
    assert result["home_active_certificates"] == 0
    assert result["waf_rules"] == 1
    assert result["legacy_waf_preserved"] is True
    assert result["legacy_ca_preserved"] is True
    assert result["ca_hostname_associated"] is False
    assert result["mtls_tunnel_route_present"] is False
    assert result["csr_subject"] == "CN=" + state["hostname"]
    assert result["validity_days"] == 365
    assert result["mutations"] is False
    assert len(mock.paths) == 6
    assert all(p.startswith("/") for p in mock.paths)


def test_plan_is_idempotent_and_rotation_coexists():
    m = module()
    s = synthetic_state(m)
    fake = fake_api(m, s)
    certs = next(v for k, v in fake.items() if "client_certificates?" in k)
    certs.append({"status": "active", "common_name": s["hostname"]})
    assert m.build_plan(s, ReadOnlyStub(fake))["next_action"] == "NOOP_CERT_EXISTS"
    certs.append({"status": "active", "common_name": s["hostname"]})
    assert m.build_plan(s, ReadOnlyStub(fake))["next_action"] == "ROTATION_IN_PROGRESS"
    certs.append({"status": "active", "common_name": s["hostname"]})
    with pytest.raises(m.Block, match="CERT_IDENTITY_OR_QUOTA_DRIFT"):
        m.build_plan(s, ReadOnlyStub(fake))


def test_plan_fails_closed_on_existing_route_or_home_waf_drift():
    m = module()
    s = synthetic_state(m)
    fake = fake_api(m, s)
    path = f"/accounts/{s['cf_account_id']}/cfd_tunnel/{s['existing_tunnel_id']}/configurations"
    fake[path]["config"]["ingress"][0]["service"] = "http://203.0.113.99"
    with pytest.raises(m.Block, match="PRODUCTION_HA_ROUTE_CHANGED"):
        m.build_plan(s, ReadOnlyStub(fake))
    fake = fake_api(m, s)
    key = f"/zones/{s['cf_zone_id']}/rulesets/phases/{m.PHASE}/entrypoint"
    fake[key]["rules"][0]["enabled"] = False
    with pytest.raises(m.Block, match="LEGACY_HOME_MTLS_WAF_CHANGED"):
        m.build_plan(s, ReadOnlyStub(fake))
    fake = fake_api(m, s)
    key = f"/zones/{s['cf_zone_id']}/certificate_authorities/hostname_associations"
    fake[key]["hostnames"] = []
    with pytest.raises(m.Block, match="LEGACY_CA_ASSOCIATION_CHANGED"):
        m.build_plan(s, ReadOnlyStub(fake))


def test_plan_refuses_mtls_route_collision_or_waf_quota():
    m = module()
    s = synthetic_state(m)
    fake = fake_api(m, s)
    key = f"/accounts/{s['cf_account_id']}/cfd_tunnel/{s['existing_tunnel_id']}/configurations"
    fake[key]["config"]["ingress"].insert(0, {
        "hostname": s["hostname"], "service": "http://203.0.113.999"
    })
    with pytest.raises(m.Block, match="MTLS_ROUTE_COLLISION"):
        m.build_plan(s, ReadOnlyStub(fake))
    fake = fake_api(m, s)
    key = f"/zones/{s['cf_zone_id']}/rulesets/phases/{m.PHASE}/entrypoint"
    fake[key]["rules"].extend({"action": "skip"} for _ in range(4))
    with pytest.raises(m.Block, match="WAF_QUOTA_EXHAUSTED"):
        m.build_plan(s, ReadOnlyStub(fake))


def test_plan_refuses_certificate_quota():
    m = module()
    s = synthetic_state(m)
    fake = fake_api(m, s)
    key = f"/zones/{s['cf_zone_id']}/client_certificates?status=all&per_page=50&page=1"
    fake[key] = [{"status": "active", "common_name": "someone-else"}] * 100
    fake[f"/zones/{s['cf_zone_id']}/client_certificates?status=all&per_page=50&page=2"] = []
    with pytest.raises(m.Block, match="CLIENT_CERT_QUOTA_EXHAUSTED"):
        m.build_plan(s, ReadOnlyStub(fake))


def test_cn_only_rsa_csr_is_disposable_and_never_logged(capsys):
    m = module()
    state = synthetic_state(m)
    saved_directory = None
    with m.disposable_csr(state["hostname"]) as (key, csr):
        assert key.is_file() and csr.is_file()
        assert key.parent == csr.parent
        assert str(key).startswith("/dev/shm/")
        assert key.stat().st_mode & 0o777 == 0o600
        assert key.parent.stat().st_mode & 0o777 == 0o700
        assert b"BEGIN PRIVATE KEY" in key.read_bytes()
        assert b"BEGIN CERTIFICATE REQUEST" in csr.read_bytes()
        saved_directory = key.parent
    assert not saved_directory.exists()
    assert not capsys.readouterr().out
    with pytest.raises(m.Block, match="CSR_CN_INVALID"):
        with m.disposable_csr("not-a-valid-mtls-host.digitalhouses.vip"):
            pass


def test_readonly_cf_client_never_issues_post(monkeypatch):
    m = module()
    methods = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, *args):
            return b'{"success": true, "result": {"safe": true}}'

    def intercepted(req, timeout=20):
        methods.append(req.get_method())
        return Response()

    monkeypatch.setattr(m.urllib.request, "urlopen", intercepted)
    client = m.ReadOnlyCloudflare("dummy-token-not-a-secret")
    assert client.get("/zones/example") == {"safe": True}
    assert methods == ["GET"]
    with pytest.raises(m.Block, match="INVALID_CF_API_PATH"):
        client.get("https://other.example")
