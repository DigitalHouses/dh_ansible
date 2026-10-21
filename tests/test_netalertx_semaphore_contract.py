"""Fail-closed contract for the first native NetAlertX Semaphore stage."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_one_new_semaphore_template_is_plan_only():
    catalog = json.loads((ROOT / "catalog/semaphore-tasks.json").read_text())
    matches = [task for task in catalog["tasks"] if "NetAlertX" in task["title"]]
    assert len(matches) == 1
    assert matches[0] == {
        "title": "60-NetAlertX · Развёртывание",
        "playbook": "playbooks/request-netalertx-install.yml",
        "view": "02 · Развёртывание",
    }


def test_semaphore_entrypoint_has_no_privileged_execution():
    play = (ROOT / "playbooks/request-netalertx-install.yml").read_text()
    role = (ROOT / "roles/netalertx_deployment_plan/tasks/main.yml").read_text()
    assert "hosts: site_runner" in play
    assert "become: false" in play
    assert "role: netalertx_deployment_plan" in play
    assert "PLAN_ONLY" in role
    assert "installation: NOT_PERFORMED" in role
    forbidden = ("pct create", "pct start", "pct stop", "pct destroy",
                 "sudo", "docker", "shell:", "delegate_to:")
    assert all(item not in play + role for item in forbidden)
    assert "protected_vmids" in role
    assert "provision_approved" in role
    assert "dhcp_exclusion_verified" in role
    assert "api_lan_isolation_verified" in role
    assert "netalertx.hostname == 'netalertx-' ~ site.id" in role
