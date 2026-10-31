"""Offline contract for the four-stage, fresh-only Proxmox Bootstrap."""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = (ROOT / "bootstrap/install.sh").read_text(encoding="utf-8")
TASK_00 = (ROOT / "bootstrap/task-00-setup.sh").read_text(encoding="utf-8")


def test_bootstrap_is_small_and_has_exactly_four_stages():
    assert len(INSTALLER.splitlines()) <= 130
    assert re.findall(r"=== ([1-4])/4", INSTALLER) == ["1", "2", "3", "4"]
    assert "bootstrap/semaphore-guest-install.py" in INSTALLER
    assert "bootstrap/task-00-setup.sh" in INSTALLER
    assert re.search(r'CODE_RELEASE="[0-9a-f]{40}"', INSTALLER)


def test_pve_is_only_used_for_fresh_container_creation():
    assert "pct create" in INSTALLER
    assert "pct start" in INSTALLER
    assert "pct destroy" not in INSTALLER
    assert "pct stop" not in INSTALLER
    assert "--unprivileged 1" in INSTALLER
    assert "--protection 1" in INSTALLER
    assert "umask 022; pct create" in INSTALLER
    assert "--resume" not in INSTALLER


def test_bootstrap_never_handles_private_fleet_or_media_workers():
    forbidden = ("gh auth", "GH_CONFIG_DIR", "FLEET_RELEASE",
                 "fleet.vault", "pve-identities", "stage_pve_access_audit",
                 "media_guest_setup", "CODE_FILES")
    for token in forbidden:
        assert token not in INSTALLER
    assert "/opt/ansible/venv" not in INSTALLER


def test_only_task_zero_is_seeded():
    assert "00-Обновить список задач" in TASK_00
    assert "playbooks/request-semaphore-catalog-sync.yml" in TASK_00
    assert "--admin-password-file" in TASK_00
    assert "dh-semaphore-catalog-sync.path" in TASK_00
    assert "autorun" in TASK_00
    assert "NO_INFRASTRUCTURE_TASKS_LAUNCHED" not in INSTALLER
