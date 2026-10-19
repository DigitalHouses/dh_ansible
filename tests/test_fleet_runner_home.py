"""Generic runner inventory and read-only audit contract.

All values come from synthetic CI fixture, never from a real home's inventory.
"""
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/site"


def test_example_runner_inventory_identity_and_reserved_address():
    all_vars = yaml.safe_load((FIXTURE / "group_vars/all.yml").read_text())
    runner = yaml.safe_load((FIXTURE / "runner.yml").read_text())["site_runner"]
    pve = yaml.safe_load((FIXTURE / "pve.yml").read_text())["pve"]
    site = all_vars["site"]["id"]
    assert site == "ci-example"
    assert all_vars["site"]["owner_slug"] + "-" + all_vars["site"]["type"] == site
    assert runner["vmid"] == 1231
    assert runner["hostname"] == f"ansible-{all_vars['site']['owner_slug']}"
    assert runner["network"]["ipv4"] == "198.51.100.231/24"
    assert pve["host"] == "198.51.100.30"
    assert runner["management"]["write_ready"] is False
    assert runner["management"]["pve_credentials_configured"] is False


def test_runner_audit_reuses_shared_role_and_private_inventory_dir():
    playbook = (ROOT / "playbooks/audit-runner.yml").read_text()
    tasks = (ROOT / "roles/runner_audit/tasks/main.yml").read_text()
    assert "role: runner_audit" in playbook
    assert "runner_audit_repository_path" in playbook
    assert "playbook_dir | dirname" in playbook
    assert "runner_audit_repository_path | default(site_runner.bootstrap.repository_path)" in tasks
    assert "{{ inventory_dir }}/group_vars/all.yml" in playbook
    assert "{{ inventory_dir }}/runner.yml" in playbook
    assert "{{ playbook_dir }}/../inventories/" not in playbook
    assert "ansible_connection" in playbook
    assert "inventory_hostname in ['runner', 'localhost']" in playbook
    assert "ansible-[a-z][a-z0-9-]*" in playbook
    assert "changed_when: false" in playbook
    assert "ansible.builtin.uri" not in playbook
    assert "ansible.builtin.shell" not in playbook
    assert "ansible.builtin.copy" not in playbook
    assert "ansible.builtin.template" not in playbook


def test_runner_ci_syntax_check_is_generic():
    ci = (ROOT / ".github/workflows/ci.yml").read_text()
    assert "tests/fixtures/syntax_inventory.yml playbooks/audit-runner.yml" in ci
    assert "inventories/home/hosts.yml" not in ci
    assert "inventories/amir/hosts.yml" not in ci
