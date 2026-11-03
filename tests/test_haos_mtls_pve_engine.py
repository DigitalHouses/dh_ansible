"""Offline regression tests for the Cloudflare HAOS mTLS PVE worker.

No Cloudflare account, certificate material, systemd or production network used.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
FILE = ROOT / "bootstrap" / "dh-cloudflare-pve-haos-mtls.py"
SPEC = importlib.util.spec_from_file_location("dh_haos_mtls_pve", FILE)
pve = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pve)

CF = {"site_id": "serzhan-home", "zone_id": "z" * 32,
      "account_id": "a" * 32,
      "new_tunnel_id": "00000000-0000-0000-0000-000000000001"}
HA = {"hostname": "serzhan-home-ha-mtls.digitalhouses.vip",
      "ipv4": "192.0.2.33", "port": 80}


def test_one_cert_per_site_refuses_duplicate_active_identities():
    certs = [
        {"common_name": HA["hostname"], "status": "active"},
        {"common_name": HA["hostname"], "status": "active"},
    ]
    with patch.object(pve, "certificate_inventory", return_value=certs):
        try:
            pve.certificate("unused", CF, HA)
        except pve.Block as error:
            assert str(error) == "MULTIPLE_ACTIVE_HOUSEHOLD_CERTIFICATES"
        else:
            raise AssertionError("should not issue a second certificate")


def test_waf_guard_must_match_verified_not_revoked_and_exact_cn():
    ruleset = {"id": "rule-set", "rules": [
        {"id": "first", "description": pve.RULE_NAME, "action": "block",
         "enabled": True, "expression": pve.EXPRESSION},
    ]}
    with patch.object(pve, "api", return_value=ruleset) as call:
        pve.changed = False
        pve.guard("unused", CF, HA)
        assert not pve.changed
        assert call.call_count == 1
    bad = {"id": "rule-set", "rules": [
        {"id": "first", "description": pve.RULE_NAME, "action": "block",
         "enabled": True, "expression": "(not cf.tls_client_auth.cert_verified)"},
    ]}
    with patch.object(pve, "api", return_value=bad):
        try:
            pve.guard("unused", CF, HA)
        except pve.Block as error:
            assert str(error) == "FLEET_WAF_EXPRESSION_DRIFT"
        else:
            raise AssertionError("must refuse missing revocation and CN protections")


def test_waf_skip_cannot_precede_the_mtls_block():
    ruleset = {"id": "x", "rules": [
        {"id": "old", "action": "skip"},
        {"id": "guard", "description": pve.RULE_NAME,
         "enabled": True, "action": "block", "expression": pve.EXPRESSION},
    ]}
    with patch.object(pve, "api", return_value=ruleset):
        try:
            pve.guard("unused", CF, HA)
        except pve.Block as error:
            assert str(error) == "WAF_SKIP_PRECEDES_MTLS_GUARD"
        else:
            raise AssertionError("skip rule before guard must be rejected")


def test_ca_preserves_all_existing_associations():
    calls = []
    hosts = ["amir-office-ha-mtls.digitalhouses.vip"]
    def api(_token, method, path, payload=None):
        calls.append((method, path, payload))
        if method == "PUT":
            hosts.extend([x for x in payload["hostnames"] if x not in hosts])
        return {"hostnames": list(hosts)}
    with patch.object(pve, "api", side_effect=api):
        pve.changed = False
        pve.ca_association("unused", CF, HA)
        assert pve.changed
        assert "amir-office-ha-mtls.digitalhouses.vip" in hosts
        assert HA["hostname"] in hosts
        pve.changed = False
        pve.ca_association("unused", CF, HA)
        assert not pve.changed
        assert len([m for m, *_ in calls if m == "PUT"]) == 1


def test_tunnel_preserves_pve_route_and_404_catchall_idempotently():
    existing = {"ingress": [
        {"hostname": "dh-serzhan-home-pve.digitalhouses.vip",
         "service": "https://192.0.2.30:8006", "originRequest": {"noTLSVerify": True}},
        {"service": "http_status:404"},
    ]}
    calls = []
    def api(_token, method, path, payload=None):
        calls.append((method, path, payload))
        if method == "GET" and path.endswith("/configurations"):
            return {"config": existing}
        if method == "GET":
            return {"name": CF["site_id"], "config_src": "cloudflare"}
        if method == "PUT":
            existing.update(payload["config"])
            return {"config": existing}
        raise AssertionError("unexpected API method")
    with patch.object(pve, "api", side_effect=api):
        pve.changed = False
        pve.tunnel("unused", CF, HA)
        assert pve.changed
        assert existing["ingress"][0]["hostname"] == "dh-serzhan-home-pve.digitalhouses.vip"
        assert existing["ingress"][-1] == {"service": "http_status:404"}
        assert len(existing["ingress"]) == 3
        pve.changed = False
        pve.tunnel("unused", CF, HA)
        assert not pve.changed
        assert len([m for m, *_ in calls if m == "PUT"]) == 1


def test_existing_dns_collision_refused():
    with patch.object(pve, "api", return_value=[
        {"type": "A", "content": "192.0.2.33", "proxied": False}
    ]):
        try:
            pve.dns("unused", CF, HA)
        except pve.Block as error:
            assert str(error) == "HAOS_DNS_READBACK_FAILED"
        else:
            raise AssertionError("incompatible record must be rejected")
