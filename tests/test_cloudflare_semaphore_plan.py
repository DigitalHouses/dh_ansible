"""Guard the nonprivileged Semaphore Cloudflare planning template."""
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
PLAYBOOK = ROOT / "playbooks/plan-cloudflare-tunnel.yml"
CATALOG = ROOT / "catalog/semaphore-tasks.json"


def test_cloudflare_plan_is_local_readonly_and_has_explicit_new_names():
    data = yaml.safe_load(PLAYBOOK.read_text())
    assert len(data) == 1
    play = data[0]
    assert play["hosts"] == "site_runner"
    assert play["become"] is False
    assert play["gather_facts"] is False
    text = PLAYBOOK.read_text()
    assert "dh-{{ site.id }}-pve." in text
    assert "cloudflare_expected_tunnel: \"{{ site.id }}\"" in text
    assert "cloudflare_pve_origin: \"https://{{ pve.host }}:{{ pve.port }}\"" in text
    assert "API_READBACK_REQUIRED" in text
    assert "NOT_AUTHORIZED_FROM_SEMAPHORE" in text
    assert "infrastructure_changes: NONE" in text
    for task in play["pre_tasks"] + play["tasks"]:
        dangerous = {
            "ansible.builtin.shell", "ansible.builtin.raw",
            "ansible.builtin.copy", "ansible.builtin.lineinfile",
            "ansible.builtin.replace", "ansible.builtin.template",
            "ansible.builtin.service", "ansible.builtin.systemd",
        }
        assert not (dangerous & task.keys())
        uri = task.get("ansible.builtin.uri")
        if uri:
            assert uri["method"] == "GET"
        cmd = task.get("ansible.builtin.command")
        if cmd:
            assert cmd["argv"][0] in {"hostname", "getent"}


def test_catalog_task_10_retains_id_and_history_safe_playbook_binding():
    import json
    data = json.loads(CATALOG.read_text())
    task = next(x for x in data["tasks"] if x["title"] == "10-Создание туннеля на PVE")
    assert task["playbook"] == "playbooks/request-cloudflare-cutover.yml"
