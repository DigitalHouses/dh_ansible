"""Canonical new PVE media VM identity and idempotent read-only preflight."""
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_media_vm_preflight_catalog_is_read_only():
    import json
    catalog = json.loads((ROOT / "catalog/semaphore-tasks.json").read_text())
    entries = [x for x in catalog["tasks"] if x["title"].startswith("80-Медиасервер")]
    assert len(entries) == 1
    assert entries[0]["playbook"] == "playbooks/plan-pve-media-vm.yml"
    roles = yaml.safe_load((ROOT / entries[0]["playbook"]).read_text())[0]["roles"]
    assert "pve_audit" in str(roles)
    assert "pve_media_vm_plan" in str(roles)
    assert "pve_media_vm_apply" not in str(roles)


def test_media_vm_identity_uses_service_site_ip_octet_rule():
    tasks = yaml.safe_load((ROOT / "roles/pve_media_vm_plan/tasks/main.yml").read_text())
    assertions = tasks[0]["ansible.builtin.assert"]["that"]
    rule = "\n".join(assertions)
    assert "media_vm.name == 'media-server-' ~ site.id" in rule
    assert "media_vm.vmid | int == 2000 +" in rule
    assert "media_vm.network.ipv4.split('/')[0].split('.')[-1]" in rule
    assert "range(200, 255)" in rule


def test_no_implicit_legacy_guest_cutover():
    role = (ROOT / "roles/pve_media_vm_plan/tasks/main.yml").read_text()
    for forbidden in (
        "qm stop", "qm set", "qm destroy", "qm create",
        "ansible.builtin.command", "ansible.builtin.shell",
        "method: POST", "method: PUT", "method: DELETE",
    ):
        assert forbidden not in role
    assert "TARGET_VMID_OCCUPIED" in role
    assert "GPU_ALREADY_ASSIGNED" in role
    assert "SATA_ALREADY_ASSIGNED" in role
    assert "ENABLED_BACKUP_TARGET_OFFLINE" in role
