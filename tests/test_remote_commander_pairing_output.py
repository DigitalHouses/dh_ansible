"""Task 40 native Ansible pairing: current invocation, sanitized output, no false Online."""
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[1]
PLAY = yaml.safe_load((ROOT / "playbooks/request-runner-remote-commander.yml").read_text())[0]
PRE = PLAY["pre_tasks"]
TASKS = PLAY["tasks"]


def task(name):
    return next(row for row in TASKS if row["name"] == name)


def test_shared_ansible_privilege_not_special_remote_worker():
    assert PLAY["hosts"] == "site_runner"
    assert PLAY["become"] is False
    assert task("Verify reusable administrative access to this managed Linux target")[
        "ansible.builtin.include_role"]["name"] == "infrastructure_access"
    install = task("Install and manage Remote Commander through the existing Ansible role")
    assert install["ansible.builtin.include_role"]["name"] == "runner_remote_commander"
    assert install["ansible.builtin.include_role"]["apply"]["become"] is True


def test_current_service_invocation_only_prevents_stale_pairing():
    source = str(TASKS)
    assert "systemctl" in source and "InvocationID" in source
    assert "_SYSTEMD_INVOCATION_ID={{ remote_service_invocation.stdout }}" in source
    assert "^[a-f0-9]{32}$" in source
    reader = task("Read only filtered device-pairing status from this service invocation")
    assert "--since '-13 min'" in reader["ansible.builtin.shell"]
    assert reader["no_log"] is True
    assert reader["changed_when"] is False


def test_current_code_or_connected_status_required_before_display():
    names = [row["name"] for row in TASKS]
    gate = "Require actual authorization status or fresh device code"
    output = "Show the confirmation code if browser pairing is needed"
    assert names.index(gate) < names.index(output)
    assert "remote_pair_status.stdout.startswith('CODE|')" in str(task(gate))
    assert "remote_pair_status.stdout == 'CONNECTED'" in str(task(gate))
    assert "remote_pair_status.stdout.split('|')[1]" in str(task(output))
    assert "https://mcp.desktopcommander.app/device/verify" in str(task(output))
    assert task(output)["when"] == "remote_pair_status.stdout.startswith('CODE|')"


def test_diagnostic_only_classified_stage_not_unfiltered_journals():
    reader = task("Read only filtered device-pairing status from this service invocation")
    script = reader["ansible.builtin.shell"]
    assert 'print("WAITING|CODE_NOT_PARSED")' in script
    assert 'print("WAITING|DEVICE_STARTUP_FAILED")' in script
    assert 'print("WAITING|NO_AUTH_LOGS_IN_INVOCATION")' in script
    assert "print(output)" not in script
    diagnostic = task("Report sanitized pairing diagnosis when no code was found")
    assert "REMOTE_COMMANDER_CODE_NOT_AVAILABLE" in str(diagnostic)


def test_service_active_does_not_fabricate_connected_or_one_time_code():
    authorized = task("Show status for an already authorized agent")
    assert authorized["when"] == "remote_pair_status.stdout == 'CONNECTED'"
    assert authorized["ansible.builtin.debug"]["msg"]["one_time_code"] == "NOT_REQUIRED"
    status = task("Report managed service, separately from provider Online state")
    assert status["ansible.builtin.debug"]["msg"]["result"] == "SERVICE_ACTIVE"
    assert "does not prove" in status["ansible.builtin.debug"]["msg"]["note"]
