"""Canonical PVE HAOS mTLS route has one shared household certificate."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WORKER_PATH = ROOT / "bootstrap/dh-cloudflare-pve-haos-mtls.py"


def _worker():
    spec = importlib.util.spec_from_file_location("dh_cloudflare_haos_mtls", WORKER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("hostname", "approved"),
    [
        ("serzhan-home-pve-ha-mtls.digitalhouses.vip", True),
        ("amir-office-pve-ha-mtls.digitalhouses.vip", True),
        ("serzhan-home-ha-mtls.digitalhouses.vip", False),
        ("dh-serzhan-home-haos.digitalhouses.vip", False),
        ("serzhan-home-pve-ha-mtls.evil.example", False),
        ("other-home-pve-ha-mtls.digitalhouses.vip", False),
    ],
)
def test_pve_worker_rejects_noncanonical_hostname(monkeypatch, tmp_path, hostname, approved):
    worker = _worker()
    private_dir = tmp_path / "material"
    private_dir.mkdir(mode=0o700)
    cf_path = tmp_path / "site.json"
    ha_path = tmp_path / "haos-mtls.json"
    cf_path.write_text(json.dumps({
        "site_id": "serzhan-home" if not hostname.startswith("amir-office-") else "amir-office",
        "account_id": "a" * 32,
        "zone_id": "b" * 32,
        "zone": "digitalhouses.vip",
        "new_tunnel_id": "19aa858a-d6ba-4d99-8958-6144337fb097",
        "pve_host": "192.168.11.30",
    }))
    site_id = json.loads(cf_path.read_text())["site_id"]
    ha_path.write_text(json.dumps({
        "site_id": site_id,
        "approved_for_publish": True,
        "certificate_policy": "one-per-home-shared-with-residents",
        "vmid": 110,
        "ipv4": "192.168.11.33",
        "port": 80,
        "hostname": hostname,
    }))
    monkeypatch.setattr(worker, "SITE_CONFIG", cf_path)
    monkeypatch.setattr(worker, "HAOS_CONFIG", ha_path)
    monkeypatch.setattr(worker, "MATERIAL", private_dir)
    monkeypatch.setattr(worker, "root_file", lambda path: path.read_text())
    monkeypatch.setattr(worker, "ip_on_host", lambda ip: ip == "192.168.11.30")
    monkeypatch.setattr(worker.os, "geteuid", lambda: 0)
    if approved:
        actual_cf, actual_ha = worker.validate_config()
        assert actual_cf["site_id"] == site_id
        assert actual_ha["hostname"] == hostname
    else:
        with pytest.raises(worker.Block, match="HAOS_HOSTNAME_DRIFT"):
            worker.validate_config()


def test_shared_waf_guard_covers_new_namespace_without_changing_fleet_guard():
    worker = _worker()
    expression = worker.EXPRESSION
    assert 'ends_with(http.host, "-ha-mtls.digitalhouses.vip")' in expression
    assert "not cf.tls_client_auth.cert_verified" in expression
    assert "cf.tls_client_auth.cert_revoked" in expression
    assert 'cert_subject_dn ne concat("CN=", http.host)' in expression
    assert "pve" not in expression  # Keep the existing WAF guard for legacy Amir too.
