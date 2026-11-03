"""Task 40 native Ansible commissioning and real Desktop Commander pairing output.

The legacy watcher files may still exist for historic sites; the current
Semaphore task must not depend on them.
"""
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
PLAYBOOK = ROOT / "playbooks/request-runner-remote-commander.yml"


def get_tasks():
    play = yaml.safe_load(PLAYBOOK.read_text(encoding="utf-8"))[0]
    assert play["hosts"] == "site_runner"
    assert play["gather_facts"] is False
    assert play["become"] is False
    return play["tasks"]


def get_pairing_parser():
    task = next(t for t in get_tasks() if t["name"].startswith(
        "Read only filtered device-pairing status"))
    shell = task["ansible.builtin.shell"]
    assert task["no_log"] is True
    assert "_SYSTEMD_INVOCATION_ID=" in shell
    assert "journalctl" in shell
    code = shell.split("python3 -c '\n", 1)[1].rsplit("\n'", 1)[0]
    return code


def parse_journal(text):
    result = subprocess.run(
        [sys.executable, "-c", get_pairing_parser()],
        input=text, text=True, capture_output=True, timeout=5, check=True,
    )
    return result.stdout.strip()


def test_task40_is_native_and_scope_limited():
    content = PLAYBOOK.read_text(encoding="utf-8")
    tasks = get_tasks()
    names = [t["name"] for t in tasks]
    assert names.index("Verify reusable administrative access to this managed Linux target") < names.index(
        "Install and manage Remote Commander through the existing Ansible role")
    assert "name: infrastructure_access" in content
    assert "name: runner_remote_commander" in content
    assert "become: true" in content
    assert "dh-remote-commander-install.path" not in content
    assert "dh-remote-commander.request" not in content
    assert "https://mcp.desktopcommander.app/device/verify" in content


def test_pairing_code_from_complete_url():
    source = (
        "Please complete authentication:\n"
        "1. Verify this device:\n"
        "https://mcp.desktopcommander.app/device/verify?user_code=ABCD-9EFG\n"
    )
    assert parse_journal(source) == "CODE|ABCD-9EFG"


def test_pairing_code_from_upstream_separate_line():
    # v0.2.52 prints code on its own line; the server-generated full URL
    # may have a different encoding or query syntax.
    source = (
        "📋 Please complete authentication:\n"
        "1. Verify this device in your browser:\n"
        "   https://mcp.desktopcommander.app/device/verify?source=remote\n"
        "2. Make sure the code matches:\n"
        "   JKL3-PQR7\n"
    )
    assert parse_journal(source) == "CODE|JKL3-PQR7"


def test_verified_device_does_not_expose_stale_code():
    source = (
        "https://mcp.desktopcommander.app/device/verify?user_code=ABCD-9EFG\n"
        "✅ Device ready:\n"
    )
    assert parse_journal(source) == "CONNECTED"


@pytest.mark.parametrize(("source", "expected"), [
    ("Secret: do-not-show\n", "WAITING|NO_AUTH_LOGS_IN_INVOCATION"),
    ("🔐 Connecting this computer...\n", "WAITING|AUTH_REQUEST_PENDING"),
    ("Device code received\n", "WAITING|CODE_NOT_PARSED"),
    ("Device startup failed: secret-do-not-show\n", "WAITING|DEVICE_STARTUP_FAILED"),
])
def test_no_code_reports_only_sanitized_stage(source, expected):
    assert parse_journal(source) == expected
