"""Semaphore task-80 composition: reusable prerequisites stay within one task."""
from pathlib import Path

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


def test_site_prerequisite_cannot_write_private_data_or_spawn_other_tasks():
    role = (ROOT / "roles/site_inventory_prepare/tasks/main.yml").read_text()
    steps = yaml.safe_load(role)
    assert steps and all(isinstance(x, dict) for x in steps)
    assert "FLEET_SOURCE_NOT_COMMISSIONED" in role
    assert "root:semaphore" in role
    assert "inventory_dir" in role
    for forbidden in (
        "method: POST", "method: PUT", "method: DELETE",
        "ansible.builtin.shell", "ansible.builtin.command",
        "ansible.builtin.copy", "ansible.builtin.fetch", "ansible.builtin.get_url",
        "qm stop", "qm set", "qm destroy",
    ):
        assert forbidden not in role


def test_no_extra_semaphore_task_was_added():
    import json
    tasks = json.loads((ROOT / "catalog/semaphore-tasks.json").read_text())["tasks"]
    assert sum(t["title"].startswith("80-Медиасервер") for t in tasks) == 1
    assert not any("Fleet Detail" in t["title"] for t in tasks)
