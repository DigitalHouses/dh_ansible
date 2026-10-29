"""Self-hosted NFS deferral must only allow the known guest to repair its own export."""
from pathlib import Path
import re
from jinja2 import Environment
import yaml

ROOT = Path(__file__).resolve().parents[1]
TASKS = ROOT / "roles/pve_media_vm_plan/tasks/main.yml"

def test_self_hosted_backup_deferral_rejects_foreign_drift():
    tasks = yaml.safe_load(TASKS.read_text())
    task = next(t for t in tasks if t["name"] == "Permit exact owned NFS backup target to recover during keep import")
    env = Environment()
    env.tests["match"] = lambda s, p: re.fullmatch(p, str(s)) is not None
    expr = env.from_string(task["ansible.builtin.set_fact"]["media_vm_self_hosted_backup_deferral_safe"])
    vm = {"name": "mediaserver-serzhan", "vmid": 2238, "network": {"ipv4": "192.168.11.238/24"},
          "storage": {"pool_name": "serzhan-pool"},
          "backup_export": {"storage_id": "dh_media_backups", "path": "/serzhan-pool/backups/pve",
                            "pve_host": "192.168.11.30",
                            "deferred_legacy_backup": {"job_id": "dh-backup-critical-daily"}}}
    cfg = {"type": "nfs", "server": "192.168.11.238", "export": "/serzhan-pool/backups/pve", "content": "backup"}
    job = {"id": "dh-backup-critical-daily", "vmid": "110,200,1011"}
    data = {"media_vm_action": "keep", "media_raw_disk_action": "import",
            "media_vm_existing_targets": [{"name": "mediaserver-serzhan", "vmid": 2238}],
            "media_vm_offline_backup_targets": ["dh_media_backups"],
            "media_vm_self_hosted_storage": {"json": {"data": cfg}},
            "media_vm_self_backup_jobs": [job], "media_vm": vm, "pve": {"host": "192.168.11.30"}}
    def allowed():
        return expr.render(**data).strip() == "True"
    assert allowed()
    data["media_vm_action"] = "recreate"
    assert not allowed()
    data["media_vm_action"] = "keep"
    data["media_vm_offline_backup_targets"] = ["dh_media_backups", "unexpected_nfs"]
    assert not allowed()
    data["media_vm_offline_backup_targets"] = ["dh_media_backups"]
    cfg["server"] = "192.168.11.99"
    assert not allowed()
    cfg["server"] = "192.168.11.238"
    job["vmid"] = "110,2238"
    assert not allowed()
    job["vmid"] = "110,200"
    data["media_vm_self_backup_jobs"] = [job, dict(job)]
    assert not allowed()

def test_self_hosted_backup_verifies_actual_pve_storage():
    source = TASKS.read_text()
    assert "/api2/json/storage/{{ media_vm.backup_export.storage_id }}" in source
    assert "media_vm_self_hosted_backup_deferral_safe" in source
    assert "ENABLED_BACKUP_TARGET_OFFLINE" in source
    assert "PVE_SELF_HOSTED_NFS_RECOVERY_DEFERRED" in source
