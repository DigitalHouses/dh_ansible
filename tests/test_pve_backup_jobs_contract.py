"""Offline contract checks for native Proxmox scheduled guest backup jobs."""
from pathlib import Path
import pytest

import yaml


ROOT = Path(__file__).resolve().parents[1]


def contents(path):
    return (ROOT / path).read_text(encoding="utf-8")


@pytest.mark.private_fleet
def test_amir_scope_is_only_runner():
    data = yaml.safe_load(contents("inventories/amir/pve.yml"))["pve"]
    jobs = data["backup_jobs"]
    assert len(jobs) == 1
    job = jobs[0]
    assert job["id"] == "dh-amir-runner"
    assert job["vmids"] == [1900]
    assert job["mode"] == "snapshot"
    assert job["schedule"] == "03:00"
    assert job["prune_backups"] == "keep-last=7"
    assert job["compress"] == "zstd"
    assert job["enabled"] is True
    assert job["storage"] == "{{ pve.backup_storage.id }}"
    assert data["backup_acceptance"]["job_id"] == job["id"]
    assert data["backup_acceptance"]["restore_ctid"] != 1900


def test_roles_are_generic_and_fail_closed():
    audit = contents("roles/pve_backup_jobs_audit/tasks/main.yml")
    plan = contents("roles/pve_backup_jobs_plan/tasks/main.yml")
    apply = contents("roles/pve_backup_jobs_apply/tasks/main.yml")
    assert "/cluster/backup" in audit
    assert "/cluster/resources?type=vm" in audit
    assert "pve.backup_jobs" in audit
    assert "lvmthin" in audit
    assert "/nodes/{{ pve.node }}/storage" in audit
    assert "/api2/json/storage/" not in audit
    assert "rootfs" in audit
    assert "pve_backup_jobs_observed" in plan
    assert "'BLOCK'" in plan
    assert "'CREATE'" in plan
    assert "'NOOP'" in plan
    assert "'UPDATE'" in plan
    assert "Managed by Ansimble-2:" in plan
    assert "'create' if item.action == 'CREATE' else 'set'" in apply
    assert "delegate_to: pve_write_host" in apply
    assert "'--vmid', item.desired.vmid" in apply
    assert "no_log: true" in apply
    assert "DELETE" not in apply
    assert "rm -" not in apply


def test_playbook_lifecycle_and_no_implicit_backup_execution():
    audit = yaml.safe_load(contents("playbooks/audit-pve-backup-jobs.yml"))[0]
    plan = yaml.safe_load(contents("playbooks/plan-pve-backup-jobs.yml"))[0]
    apply = yaml.safe_load(contents("playbooks/apply-pve-backup-jobs.yml"))[0]
    roles = lambda p: [r["role"] for r in p["roles"]]
    assert roles(audit) == ["fleet_secrets", "pve_backup_jobs_audit"]
    assert roles(plan) == ["fleet_secrets", "pve_backup_jobs_audit", "pve_backup_jobs_plan"]
    assert roles(apply) == ["fleet_secrets", "pve_host_write_access", "pve_backup_jobs_audit", "pve_backup_jobs_plan", "pve_backup_jobs_apply"]
    assert "pve_backup_jobs_audit" in contents("playbooks/apply-pve-backup-jobs.yml")
    assert "post_apply: NOOP" in contents("playbooks/apply-pve-backup-jobs.yml")
    assert "/vzdump" not in contents("roles/pve_backup_jobs_apply/tasks/main.yml")


def test_one_time_backup_cannot_repeat_or_prune():
    one = contents("playbooks/run-pve-backup-once.yml")
    assert "selectattr('vmid', 'equalto', pve.backup_jobs[0].vmids[0] | int)" in one
    assert "pve_backup_once_matches[0].action == 'NOOP'" in one
    assert "remove: 0" in one
    assert "/vzdump" in one
    assert "exitstatus == 'OK'" in one
    assert "vzdump-lxc-" in one


def test_proxmox_api_prune_shapes_match_offline_ansible_plan():
    """Run the actual reusable planner against both API field representations."""
    import shutil
    import subprocess

    executable = shutil.which("ansible-playbook")
    assert executable, "Ansible CLI is required for plan regression tests"
    result = subprocess.run(
        [executable, "-i", "localhost,", "tests/fixtures/pve_backup_jobs_plan_shapes.yml"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )
    assert result.returncode == 0, result.stdout[-8000:] + result.stderr[-3000:]


def test_archive_verification_uses_storage_list_and_local_vmid_filter():
    one = contents("playbooks/run-pve-backup-once.yml")
    assert "content?content=backup&vmid=" not in one
    assert one.count('"/nodes/{{ pve.node }}/storage/{{ pve.backup_storage.id }}/content"') == 2
    assert "delegate_to: pve_write_host" in one
    assert one.count("from_json") >= 2
    assert "selectattr('vmid', 'equalto', pve.backup_jobs[0].vmids[0] | int)" in one
    assert "pve_backup_once_source_archives | length == 1" in one


@pytest.mark.private_fleet
def test_restore_acceptance_is_isolated_and_never_started():
    import re

    data = yaml.safe_load(contents("inventories/amir/pve.yml"))["pve"]
    assert data["backup_acceptance"]["restore_ctid"] == 1990
    assert data["backup_acceptance"]["restore_storage"] == "local-lvm"

    restore = contents("playbooks/accept-pve-backup-restore.yml")
    assert "pve.backup_acceptance.restore_ctid" in restore
    assert "pve_backup_jobs_guests.json.data" in restore
    assert "pve_backup_jobs_plan" in restore
    assert "- --force\n          - \"0\"" in restore
    assert "- --start\n          - \"0\"" in restore
    assert "- --onboot\n          - \"0\"" in restore
    assert "- --protection\n          - \"1\"" in restore
    assert "link_down=1,ip=manual" in restore
    assert "pve_restore_task_matches | length == 1" in restore
    assert "pve_backup_restore_isolation" in restore
    assert "pve_restore_status.json.data.status == 'stopped'" in restore
    assert "pve_restore_config.json.data.net0" in restore
    assert '"/nodes/{{ pve.node }}/storage/{{ pve.backup_storage.id }}/content"' in restore
    assert "pve_restore_archive_inventory.stdout | from_json" in restore
    assert "DELETE" not in restore
    assert "pct start" not in restore


def test_restore_network_quarantine_is_bound_to_managed_stopped_guest():
    quarantine = contents("roles/pve_backup_restore_isolation/tasks/main.yml")
    playbook = yaml.safe_load(contents("playbooks/apply-pve-restore-isolation.yml"))[0]
    roles = [entry["role"] for entry in playbook["roles"]]
    assert roles == ["fleet_secrets", "pve_host_write_access", "pve_backup_restore_isolation"]
    assert "Managed by Ansimble-2: isolated restore verification of CT" in quarantine
    assert "pve_restore_quarantine_status_before.json.data.status == 'stopped'" in quarantine
    assert "pve_restore_quarantine_config_before.json.data.protection" in quarantine
    assert "pve_restore_quarantine_config_before.json.data.onboot" in quarantine
    assert "link_down=1,ip=manual" in quarantine
    assert "pve_restore_quarantine_status_after.json.data.status == 'stopped'" in quarantine
    assert "'NOOP'" in quarantine
    assert "'UPDATE'" in quarantine
    assert "delegate_to: pve_write_host" in quarantine
    assert "DELETE" not in quarantine
    assert "pct start" not in quarantine
