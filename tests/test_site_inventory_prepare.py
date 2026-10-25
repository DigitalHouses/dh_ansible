"""Semaphore task-80 orchestration: site-data is an internal prerequisite."""
from pathlib import Path
import json
import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_task80_has_site_dependency_before_load_vars():
    play = yaml.safe_load((ROOT / "playbooks/plan-pve-media-vm.yml").read_text())[0]
    pre = play["pre_tasks"]
    names = [t["name"] for t in pre]
    assert names.index("Resolve reusable site inventory prerequisite") < names.index(
        "Load desired media server VM state"
    )
    dep = next(t for t in pre if t["name"] == "Resolve reusable site inventory prerequisite")
    assert dep["ansible.builtin.include_role"]["name"] == "site_inventory_prepare"
    assert dep["vars"]["site_inventory_required_files"] == ["media_vm.yml"]


def test_site_prerequisite_uses_root_worker_and_no_private_git_tokens():
    main = (ROOT / "roles/site_inventory_prepare/tasks/main.yml").read_text()
    one = (ROOT / "roles/site_inventory_prepare/tasks/refresh-one.yml").read_text()
    assert yaml.safe_load(main) and yaml.safe_load(one)
    assert "SITE_DATA_PROVIDER_NOT_COMMISSIONED" in main
    assert "site_inventory_required_files" in main
    assert "site_inventory_required_file" in one
    assert "dh-site-data.request" in one
    assert "dh-site-data.status" in one
    assert "site_data_nonce.stdout" in one
    assert "root:semaphore" in main
    for forbidden in ("github_pat_", "GH_TOKEN", "GITHUB_TOKEN",
                      "git clone", "vault.yml", "method: POST",
                      "become: true"):
        assert forbidden not in one


def test_no_extra_semaphore_template():
    catalog = json.loads((ROOT / "catalog/semaphore-tasks.json").read_text())["tasks"]
    assert sum(t["title"].startswith("80-Медиасервер") for t in catalog) == 1
    assert not any(t["title"].startswith("05-") for t in catalog)
