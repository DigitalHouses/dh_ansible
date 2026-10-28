"""Windows Network discovery identity must match the authenticated Samba server.

No DNS records, credentials or web access are modified by this contract.
"""
from pathlib import Path
import yaml

ROOT=Path(__file__).resolve().parents[1]
ROLE=ROOT/"roles/media_guest_setup"


def test_wsdd2_and_samba_share_the_approved_identity():
    smb=(ROLE/"templates/media-smb.conf.j2").read_text()
    disc=(ROLE/"tasks/discovery_portal.yml").read_text()
    assert "netbios name = {{ media_vm.discovery.netbios_name }}" in smb
    assert "-H {{ media_vm.discovery.netbios_name }}" in disc
    assert "-N {{ media_vm.discovery.netbios_name }}" in disc
    assert "-G {{ media_vm.discovery.workgroup }}" in disc
    assert "media_vm.network.guest_interface" in disc
    assert "MEDIA-SERZHAN" not in disc


def test_port_5355_is_not_shared_with_systemd_resolved():
    tasks=yaml.safe_load((ROLE/"tasks/discovery_portal.yml").read_text())
    by_name={t["name"]:t for t in tasks}
    resolver=by_name["Leave LLMNR responder port 5355 exclusively to wsdd2"]
    assert resolver["ansible.builtin.copy"]["content"] == "[Resolve]\nLLMNR=no\n"
    assert "50-dh-wsdd2-llmnr.conf" in resolver["ansible.builtin.copy"]["dest"]
    assert by_name["Release LLMNR sockets held by systemd-resolved"]["when"] == "media_resolved_llmnr.changed"
    assert by_name["Restart WSD discovery only if name or LLMNR policy changed"]["when"] == "media_wsdd2_identity.changed or media_resolved_llmnr.changed"


def test_discovery_change_is_idempotent_and_scope_is_limited():
    tasks=yaml.safe_load((ROLE/"tasks/discovery_portal.yml").read_text())
    target=[t for t in tasks if "WSD" in t["name"] or "LLMNR" in t["name"] or "wsdd2" in t["name"]]
    assert target
    assert all("smbd.service" not in str(t) for t in target)
    assert all("nmbd.service" not in str(t) for t in target)
    assert any("systemctl, show, wsdd2.service" in str(t) for t in target)
