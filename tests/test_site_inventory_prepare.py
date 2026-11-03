"""Current native Task 80 site scoping and reusable runner inventory contracts."""
from pathlib import Path
import json
import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_task80_verifies_private_site_identity_before_native_pve_apply():
    plays = yaml.safe_load((ROOT / "playbooks/plan-pve-media-vm.yml").read_text())
    assert len(plays) == 4
    first = plays[0]
    text = str(first)
    assert "media_site_repo.json.private == true" in text
    assert "media_site_repo.json.name == 'site-' ~ site.id" in text
    assert "MEDIA_SITE_MARKER_MISMATCH" in text
    assert "media_site_file.json.sha is match('^[0-9a-f]{40}$')" in text
    assert "MEDIA_NATIVE_VM_DISK_IDENTITY_INVALID" in text
    assert "MEDIA_PVE_HARDWARE_PREFLIGHT_BLOCKED" in str(plays[1])


def test_reusable_site_prerequisite_uses_native_ansible_and_strict_file_scope():
    main = (ROOT / "roles/site_inventory_prepare/tasks/main.yml").read_text()
    tasks = yaml.safe_load(main)
    assert isinstance(tasks, list)
    assert "site_inventory_required_files" in main
    assert "SITE_DATA_IDENTITY_BLOCKED" in main
    assert "SITE_DATA_SCOPE_BLOCKED" in main
    assert "SITE_DATA_FLEET_NOT_READY" in main
    assert "infrastructure_access" in main
    assert "owner: root" in main and "group: semaphore" in main
    assert 'mode: "0640"' in main
    assert "become: true" in main
    for forbidden in ("github_pat_", "GH_TOKEN", "GITHUB_TOKEN",
                      "git clone", "method: POST", "dh-site-data.request"):
        assert forbidden not in main


def test_semaphore_catalog_has_single_05_and_single_80():
    catalog = json.loads((ROOT / "catalog/semaphore-tasks.json").read_text())["tasks"]
    assert sum(t["title"].startswith("80-Медиасервер") for t in catalog) == 1
    assert sum(t["title"].startswith("05-") for t in catalog) == 1
    assert sum(t["title"].startswith("06-") for t in catalog) == 1
