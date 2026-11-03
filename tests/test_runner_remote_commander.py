"""Portable, synthetic-only contracts for the runner Remote Commander playbook."""
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[1]
ROLE = ROOT / "roles/runner_remote_commander"


def test_remote_commander_playbook_is_local_runner_only():
    text = (ROOT / "playbooks/apply-runner-remote-commander.yml").read_text()
    data = yaml.safe_load(text)
    assert len(data) == 1
    play = data[0]
    assert play["hosts"] == "site_runner"
    assert play["gather_facts"] is False
    assert play["roles"] == [{"role": "runner_remote_commander"}]
    assert "ansible_play_hosts_all | length == 1" in text
    assert "ansible_connection | default('')" in text


def test_pinned_releases_and_approved_root_only_user():
    defaults = yaml.safe_load((ROLE / "defaults/main.yml").read_text())
    assert defaults["remote_commander_node_version"] == "v24.22.0"
    assert defaults["remote_commander_package_version"] == "0.2.52"
    assert defaults["remote_commander_service_name"] == "digitalhouses-remote-commander.service"
    assert set(defaults["remote_commander_node_checksums"]) == {"x86_64", "aarch64"}
    assert all(len(x) == 64 for x in defaults["remote_commander_node_checksums"].values())
    unit = (ROLE / "templates/digitalhouses-remote-commander.service.j2").read_text()
    assert "User=root" in unit
    assert "Environment=HOME=/root" in unit
    assert "WorkingDirectory=/root" in unit
    assert "--dns-result-order=ipv4first --network-family-autoselection-attempt-timeout=2000" in defaults["remote_commander_node_network_options"]
    assert "ExecStart=/opt/dh-node/bin/desktop-commander remote" in unit
    assert "WantedBy=multi-user.target" in unit
    assert "Restart=on-failure" in unit
    assert "User=ansimble" not in unit and "User=semaphore" not in unit


def test_role_installs_node_npm_service_idempotently_and_checks_manual_session():
    body = (ROLE / "tasks/main.yml").read_text()
    tasks = yaml.safe_load(body)
    names = [item["name"] for item in tasks]
    assert len(names) == len(set(names))
    assert "Fetch verified pinned Node.js LTS archive" in names
    assert "Extract pinned Node.js distribution" in names
    assert "Install exactly the approved Desktop Commander release" in names
    assert "Enable and start managed Remote Commander" in names
    assert "Detect an interactive Remote Commander session" in names
    assert "Avoid taking over an interactive session unexpectedly" in names
    assert "remote_commander_package_actual.stdout != remote_commander_package_version" in body
    assert "not remote_commander_installed_node.stat.exists" in body
    assert "remote_commander_service_before.stdout" in body
    assert "ansible.builtin.systemd_service" in body
    assert "useradd" not in body and "dhremote" not in body
    assert "pct " not in body and "qm " not in body


def test_release_plays_no_role_on_pve_and_ci_checks_syntax():
    ci = (ROOT / ".github/workflows/ci.yml").read_text()
    assert "ansible-playbook --syntax-check -i tests/fixtures/syntax_inventory.yml playbooks/apply-runner-remote-commander.yml" in ci
    role = (ROLE / "tasks/main.yml").read_text()
    assert "remote_commander_node_checksums[remote_commander_arch.stdout]" in role
    assert "HOME: /root" in role


def test_readonly_commands_execute_in_ansible_check_mode():
    import yaml
    role = yaml.safe_load((ROLE / "tasks/main.yml").read_text())
    for task in role:
        if "ansible.builtin.command" in task and task.get("changed_when") is False:
            assert task.get("check_mode") is False, task["name"]
    play = yaml.safe_load(
        (ROOT / "playbooks/apply-runner-remote-commander.yml").read_text()
    )[0]
    for task in play["pre_tasks"]:
        if "ansible.builtin.command" in task:
            assert task.get("changed_when") is False, task["name"]
            assert task.get("check_mode") is False, task["name"]
