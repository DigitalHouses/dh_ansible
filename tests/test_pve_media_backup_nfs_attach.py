"""Safety and idempotence contracts for the independent PVE Media NFS backup task."""
import json
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
PLAYBOOK = ROOT / "playbooks/attach-pve-media-backup-nfs.yml"


def test_semaphore_has_one_independent_backup_nfs_task():
    catalog = json.loads((ROOT / "catalog/semaphore-tasks.json").read_text())
    matching = [x for x in catalog["tasks"] if x["title"] == "90-PVE · Подключить NFS Backups"]
    assert len(matching) == 1
    assert matching[0]["playbook"] == "playbooks/request-pve-media-backup-nfs.yml"
    assert matching[0]["playbook"] != "playbooks/plan-pve-media-vm.yml"
    assert "survey_vars" not in matching[0]


def test_approved_fleet_export_and_pinned_ssh_only():
    plays = yaml.safe_load(PLAYBOOK.read_text())
    assert len(plays) == 2
    assert plays[0]["hosts"] == "site_runner"
    assert plays[0]["become"] is False
    assert plays[1]["hosts"] == "dh_media_nfs_pve"
    assert plays[1]["become"] is True
    setup = str(plays[0]["tasks"])
    assert "media_vm.yml" in setup
    assert "pve_access.yml" in setup
    assert "dh_media_backups" in setup
    assert "media_vm.backup_export.pve_host" in setup
    assert "media_vm.backup_export.path" in setup
    assert "StrictHostKeyChecking=yes" in setup


def test_never_delete_storage_backup_jobs_vm_or_backup_data():
    source = PLAYBOOK.read_text()
    for forbidden in (
        "pvesm remove", "qm destroy", "qm stop", "qm reboot", "vzdump ",
        "pvesh delete", "rm -rf", "umount ", "reboot ", " /cluster/backup",
        "playbooks/plan-pve-media-vm.yml",
    ):
        assert forbidden not in source
    assert "backup_jobs: UNTOUCHED" in source
    assert "backups_deleted: false" in source


def test_native_nfs_backup_is_idempotent_and_write_tested():
    plays = yaml.safe_load(PLAYBOOK.read_text())
    tasks = {t["name"]: t for t in plays[1]["tasks"]}
    create = tasks["Register native backup-only NFS storage when absent"]
    args = create["ansible.builtin.command"]["argv"]
    assert args[:4] == ["pvesm", "add", "nfs", "{{ dh_backup_storage_id }}"]
    assert args[args.index("--content") + 1] == "backup"
    assert args[args.index("--options") + 1] == "vers=4.1"
    assert create["when"] == "dh_existing_backup_storages | length == 0"
    assert "Reject duplicate or drifting storage definitions" in tasks
    assert "Require the exact NFS source, not an ordinary local directory" in tasks
    for name in (
        "Audit any existing mount at the managed PVE path",
        "Verify mounted export identity and NFS filesystem",
    ):
        assert tasks[name]["ansible.builtin.command"]["argv"][-2:] == [
            "-o", "SOURCE,FSTYPE"
        ]
    probe = tasks["Verify root-squashed NFS write, read and cleanup in backup directory"]
    assert "ansible.builtin.tempfile" in str(probe["block"])
    assert "ansible.builtin.copy" in str(probe["block"])
    assert "ansible.builtin.slurp" in str(probe["block"])
    assert "dh_backup_probe_content.content | b64decode" in str(probe["block"])
    assert "ansible.builtin.file" in str(probe["always"])
    assert "dh_backup_write_probe.path" in str(probe["always"])

def test_semaphore_only_requests_a_pinned_root_worker_action():
    request = ROOT / "playbooks/request-pve-media-backup-nfs.yml"
    plays = yaml.safe_load(request.read_text())
    assert len(plays) == 1
    assert plays[0]["hosts"] == "site_runner"
    assert plays[0]["become"] is False
    source = request.read_text()
    assert "MEDIA_NFS_ATTACH:{{ nfs_nonce.stdout }}" in source
    assert "dh-pve-access-audit.path" in source
    assert "PASS:" in source and "FAILED:" in source
    assert "pve_host_write_ed25519" not in source
    assert "ansible_ssh_private_key_file" not in source


def test_protected_worker_has_only_fixed_root_owned_playbook():
    worker = (ROOT / "bootstrap/pve-access-audit-request.sh").read_text()
    assert "AUDIT|MEDIA_NFS_ATTACH|MEDIA_PLAN_" in worker
    assert 'if [[ "$ACTION" == "MEDIA_NFS_ATTACH" ]]; then' in worker
    assert "/opt/digitalhouses/bootstrap/pve-access/playbooks/attach-pve-media-backup-nfs.yml" in worker
    assert "'root:root:644'" in worker
    assert 'report PASS' in worker
    bootstrap = (ROOT / "bootstrap/semaphore-host-install.py").read_text()
    assert '"attach-pve-media-backup-nfs.yml"' in bootstrap
