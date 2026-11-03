"""Canonical HAOS Fleet metadata permits offline Vault preparation, not issuance."""
from pathlib import Path
import importlib.util
import json
import yaml
import pytest

ROOT = Path(__file__).resolve().parents[1]


def module():
    path = ROOT / "bin/ha-mtls-vault-stage.py"
    spec = importlib.util.spec_from_file_location("vault_stage_new_site", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def write_site(root, site="sample-home", hostname=None, approved=False):
    folder = root / "inventories" / site
    (folder / "group_vars").mkdir(parents=True)
    (folder / "group_vars/all.yml").write_text(yaml.safe_dump({"site": {
        "id": site, "owner_slug": "sample", "type": "home"
    }}))
    (folder / "haos.yml").write_text(yaml.safe_dump({"haos": {"cloudflare": {
        "tunnel": site,
        "hostname": hostname or site + "-pve-ha-mtls.digitalhouses.vip",
        "approved_for_publish": approved,
        "mtls_required": True,
        "mtls_method": "cloudflare_client_cert_waf",
        "certificate_policy": "one-per-home-shared-with-residents"
    }}}))


def test_standard_site_stages_exact_pve_ha_mtls_hostname(monkeypatch, tmp_path):
    m = module()
    monkeypatch.setattr(m, "FLEET_ROOT", tmp_path)
    write_site(tmp_path)
    state = m.canonical_intent("sample-home")
    assert state["hostname"] == "sample-home-pve-ha-mtls.digitalhouses.vip"
    assert state["cert_validity_days"] == 365


@pytest.mark.parametrize("hostname,approved", [
    ("sample-home-ha-mtls.digitalhouses.vip", False),
    ("other-home-pve-ha-mtls.digitalhouses.vip", False),
    ("sample-home-pve-ha-mtls.digitalhouses.vip", True),
])
def test_standard_site_fails_closed_on_hostname_or_publish_drift(monkeypatch, tmp_path,
                                                                 hostname, approved):
    m = module()
    monkeypatch.setattr(m, "FLEET_ROOT", tmp_path)
    write_site(tmp_path, hostname=hostname, approved=approved)
    with pytest.raises(m.Block, match="CANONICAL_HAOS_MTLS_POLICY_DRIFT"):
        m.canonical_intent("sample-home")
