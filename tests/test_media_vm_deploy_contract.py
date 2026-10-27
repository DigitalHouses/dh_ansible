"""Offline contracts for one-click media VM deployment."""
import ast
from pathlib import Path
import importlib.util

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "bootstrap/dh-pve-media-deploy.py"
WORKER = ROOT / "bootstrap/pve-access-audit-request.sh"
PLAY = ROOT / "playbooks/plan-pve-media-vm.yml"


def module():
    spec = importlib.util.spec_from_file_location("dh_media_deploy", DEPLOY)
    obj = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obj)
    return obj


def test_python_and_declarative_task_syntax():
    ast.parse(DEPLOY.read_text())
    import yaml
    assert yaml.safe_load(PLAY.read_text())[0]["hosts"] == "site_runner"


def test_deploy_reuses_existing_worker_no_extra_template():
    import json
    worker = WORKER.read_text()
    play = PLAY.read_text()
    tasks = json.loads((ROOT / "catalog/semaphore-tasks.json").read_text())["tasks"]
    assert len([x for x in tasks if x["title"].startswith("80-Медиасервер")]) == 1
    assert "MEDIA_DEPLOY_(CREATE|IMPORT)_(KEEP|RECREATE)_(PRESERVE|ERASE)" in worker
    assert "MEDIA_DEPLOY_{{ media_storage_action | upper }}" in play
    assert "dh-pve-access-audit.request" in play
    assert "dh-pve-media-deploy" in worker
    assert "--apply" in worker
    assert "MEDIA_PLAN" in worker


def test_image_download_and_host_compatibility():
    code = DEPLOY.read_text()
    for term in ("debian-13-generic-amd64.qcow2", "SHA512SUMS",
                 "hashlib.sha512", "qm(\"importdisk\"", "qm(\"start\"",
                 "local-lvm", "pct(\"ssh-keygen\"", "hostpci"):
        assert term in code
    assert 'old_guest_matches(vmid, site, data)' in code
    assert 'LEGACY_ZFS_GUID_MISMATCH' in code
    assert 'LEGACY_PHYSICAL_DISK_IDENTITY_MISMATCH' in code
    assert 'qm("destroy", vmid' in code
    for forbidden in ("qm stop 501", "qm stop 700",
                      "wipefs", "mkfs", "sgdisk", "dd if="):
        assert forbidden not in code


def test_metadata_validation_before_any_download(monkeypatch):
    m = module()
    monkeypatch.setattr(m.os, "geteuid", lambda: 0)
    monkeypatch.setattr(m.Path, "is_file", lambda *_: True)
    monkeypatch.setattr(m.Path, "exists", lambda *_: True)
    monkeypatch.setattr(m, "run", lambda *a, **k: ("pve" if a[0] == "/usr/bin/hostname" else "vmbr0 inet 198.51.100.30/24"))
    monkeypatch.setattr(m, "pct", lambda *args: (
        "ansible-demo-home" if args[0] == "hostname"
        else "site:\n  id: demo-home\n  owner_slug: demo\n  type: home" if args[-1].endswith("all.yml")
        else "media_vm: {}" if args[-1].endswith("media_vm.yml")
        else "pve:\n  host: 198.51.100.30\n  node: pve"
    ))
    data, _ = m.site_data("demo-home")
    assert data == {}

def test_media_vm_ownership_after_first_success_is_idempotent():
    import yaml
    text = PLAY.read_text()
    assert "difference([media_vm.vmid | int])" in text
    role = (ROOT / "roles/pve_media_vm_plan/tasks/main.yml").read_text()
    assert "rejectattr('vmid', 'equalto', media_vm.vmid | int)" in role
    assert "80-Медиасервер · Развёртывание" in (ROOT / "catalog/semaphore-tasks.json").read_text()
