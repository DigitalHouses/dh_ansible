"""Fail-closed first-commissioning contract: DHCP and deferred legacy PVE backups."""
import copy
from pathlib import Path
import re

from jinja2 import Environment
import yaml

ROOT = Path(__file__).resolve().parents[1]


def read(path):
    return yaml.safe_load((ROOT / path).read_text())


def expression():
    role = read("roles/pve_media_vm_plan/tasks/main.yml")
    step = next(t for t in role if t.get("name") ==
                "Allow deferral only for existing VM keep+import and unrelated explicit VM job")
    expr = step["ansible.builtin.set_fact"]["media_vm_backup_deferral_safe"]
    env = Environment()
    env.tests["match"] = lambda value, pattern: re.fullmatch(pattern, str(value)) is not None
    return env.from_string(expr)


def fixture():
    return {
        "media_vm_action": "keep",
        "media_raw_disk_action": "import",
        "media_vm_existing_targets": [{"vmid": 2238}],
        "media_vm_offline_backup_targets": ["truenas_backup"],
        "media_vm": {"vmid": 2238, "backup_export": {
            "storage_id": "dh_media_backups",
            "deferred_legacy_backup": {
                "storage_id": "truenas_backup",
                "job_id": "dh-backup-critical-daily",
                "resume_after": "media_guest_accepted"}}},
        "media_vm_deferred_backup_jobs": [
            {"id": "dh-backup-critical-daily", "storage": "truenas_backup",
             "enabled": 1,
             "vmid": "110,200,333,501,1011,10001,10004"}],
    }


def is_safe(overrides=None, mutate=None):
    data = fixture()
    if overrides:
        data.update(overrides)
    if mutate:
        mutate(data)
    return expression().render(**data).strip().lower() == "true"


def test_defer_exact_pve_backup_job_only_for_keep_import_and_non_member_vmid():
    assert is_safe()
    for action in ("create", "recreate"):
        assert not is_safe({"media_vm_action": action})
    assert not is_safe({"media_raw_disk_action": "erase"})
    assert not is_safe({"media_vm_existing_targets": []})
    assert not is_safe({"media_vm_offline_backup_targets": ["other_backup"]})
    assert not is_safe({"media_vm_offline_backup_targets": [
        "truenas_backup", "other_backup"]})
    assert not is_safe(mutate=lambda d: d["media_vm_deferred_backup_jobs"][0].update(
        {"vmid": "110,2238,501"}))
    assert not is_safe(mutate=lambda d: d["media_vm_deferred_backup_jobs"][0].update(
        {"id": "foreign_backup_job"}))
    assert not is_safe(mutate=lambda d: d["media_vm_deferred_backup_jobs"][0].update(
        {"all": 1}))
    assert not is_safe(mutate=lambda d: d["media_vm_deferred_backup_jobs"][0].update(
        {"pool": "all-guests"}))
    assert not is_safe(mutate=lambda d: d["media_vm_deferred_backup_jobs"][0].update(
        {"exclude": "50"}))
    assert not is_safe(mutate=lambda d: d["media_vm_deferred_backup_jobs"][0].update(
        {"vmid": "110,2238a"}))
    assert not is_safe(mutate=lambda d: d["media_vm_deferred_backup_jobs"][0].pop(
        "vmid"))
    assert not is_safe(mutate=lambda d: d["media_vm_deferred_backup_jobs"].append(
        {"id": "second_job", "vmid": "100", "storage": "truenas_backup"}))


def test_dhcp_verified_requires_explicit_lan_range_and_outside_lease_pool():
    role = read("roles/pve_media_vm_plan/tasks/main.yml")
    clauses = next(x["ansible.builtin.assert"]["that"] for x in role
                   if x.get("name") == "Validate generic media VM desired-state contract")
    text = "\n".join(clauses)
    assert "media_vm.network.dhcp_pool is mapping" in text
    assert "media_vm.network.static_ipv4_verified is boolean" in text
    assert "media_vm.network.dhcp_pool.first_host" in text
    assert "media_vm.network.dhcp_pool.last_host" in text
    # User-authorized site DHCP leases .50-.229; VM is static .238.
    pool_first, pool_last, vm_ip = 50, 229, 238
    assert pool_last < vm_ip <= 254 and pool_first < pool_last


def test_pve_worker_carries_actions_inside_fixed_nonce_scoped_plan_request():
    survey = (ROOT / "playbooks/plan-pve-media-vm.yml").read_text()
    worker = (ROOT / "bootstrap/pve-access-audit-request.sh").read_text()
    audit = (ROOT / "playbooks/audit-pve-access.yml").read_text()
    assert "MEDIA_PLAN_{{ media_vm_action | upper }}_{{ media_raw_disk_action | upper }}" in survey
    assert "MEDIA_PLAN_(CREATE|RECREATE|KEEP)_(IMPORT|ERASE)" in worker
    assert 'media_vm_action=$MEDIA_PLAN_VM_ACTION' in worker
    assert 'media_raw_disk_action=$MEDIA_PLAN_DISK_ACTION' in worker
    assert "'requested_vm_action': media_vm_action" in audit
    assert "'requested_disk_action': media_raw_disk_action" in audit
    assert "media_plan_summary.requested_vm_action == media_vm_action" in survey
    assert "media_plan_summary.requested_disk_action == media_raw_disk_action" in survey


def test_pve_backup_mapping_is_deferred_until_separate_post_acceptance_operation():
    worker = (ROOT / "bootstrap/pve-access-audit-request.sh").read_text()
    assert "--map-backups --apply" not in worker
    assert "MEDIA_BACKUP_MIGRATION=DEFERRED_UNTIL_ACCEPTANCE" in worker
    mapper = (ROOT / "bootstrap/dh-pve-media-deploy.py").read_text()
    assert '--map-backups' in mapper
    assert "def ensure_backup_storage(" in mapper
    # A deferred backup cannot be silently removed from the preflight.
    role = read("roles/pve_media_vm_plan/tasks/main.yml")
    blocker = next(t for t in role if t.get("name") ==
                   "Calculate cutover and commissioning blockers without modifying PVE")
    assert "ENABLED_BACKUP_TARGET_OFFLINE" in str(blocker)
    assert "media_vm_backup_deferral_safe" in str(blocker)
    warning = next(t for t in role if t.get("name") ==
                   "Publish explicit backup migration warning without changing old scheduled jobs")
    assert "PVE_LEGACY_BACKUP_MIGRATION_DEFERRED" in str(warning)
    audit = (ROOT / "playbooks/audit-pve-access.yml").read_text()
    assert "'warnings': media_vm_plan_warnings" in audit
