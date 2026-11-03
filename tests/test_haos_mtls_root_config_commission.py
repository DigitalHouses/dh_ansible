"""Safe one-time PVE config must not bypass the escrow prerequisite."""
from pathlib import Path
import importlib.util

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load():
    path = ROOT / "bootstrap/haos-mtls-commission.py"
    spec = importlib.util.spec_from_file_location("haos_mtls_root_setup_test", path)
    obj = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obj)
    return obj


def test_existing_fixed_sudoers_path_and_canonical_hostname():
    m = load()
    assert str(m.SUDOERS) == "/etc/sudoers.d/dh-cloudflare-pve-haos-mtls"
    assert 'install_file(HERE / "dh-cloudflare-pve-haos-mtls.sudoers", SUDOERS, 0o440)' in (
        ROOT / "bootstrap/haos-mtls-commission.py").read_text()
    content = (ROOT / "bootstrap/haos-mtls-commission.py").read_text()
    assert 'site + "-pve-ha-mtls.digitalhouses.vip"' in content
    assert "PUBLISH_APPROVAL_REQUIRES_VAULT_ESCROW_AND_SEPARATE_REVIEW" in content
    assert "HAOS_MTLS_PUBLICATION_APPROVED=FALSE" in content


def test_approved_config_rejected_before_any_write(monkeypatch):
    from types import SimpleNamespace
    m = load()
    cf = {"site_id": "sample-home", "zone": "digitalhouses.vip",
          "new_tunnel_id": "00000000-0000-0000-0000-000000000001"}
    monkeypatch.setattr(m, "root_text", lambda *_: __import__("json").dumps(cf))
    calls=[]
    monkeypatch.setattr(m, "run", lambda *args: calls.append(args) or "")
    monkeypatch.setattr(m, "install_file", lambda *args: calls.append(args))
    with pytest.raises(m.Block, match="PUBLISH_APPROVAL_REQUIRES_VAULT_ESCROW"):
        m.pve(SimpleNamespace(vmid=110, ipv4=".".join(map(str, (192, 168, 0, 33))), approve_publish=True))
    assert calls == []
