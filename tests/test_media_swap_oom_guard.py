"""Prevent OOM in single Media VM; root ext4 swap is low-priority and idempotent."""
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[1]
TASKS = ROOT / "roles/media_guest_setup/tasks/swap_guard.yml"


def test_swap_guard_is_in_provisioning_before_apps():
    s = (ROOT / "roles/media_guest_setup/tasks/main.yml").read_text()
    assert "import_tasks: swap_guard.yml" in s
    assert s.index("import_tasks: swap_guard.yml") < s.index("import_tasks: apps.yml")
    boot = (ROOT / "bootstrap/semaphore-host-install.py").read_text()
    assert '"roles/media_guest_setup/tasks/swap_guard.yml"' in boot
    assert '("media_guest_setup", "tasks/swap_guard.yml")' in boot


def test_guest_swap_is_ext4_guarded_and_not_zfs_pool():
    s = TASKS.read_text()
    assert "MEDIA_SWAP_ROOT_NOT_EXT4" in s
    assert "MEDIA_SWAP_FILE_UNSAFE_OR_DRIFTED" in s
    assert "2147483648" in s
    assert "creates: /swapfile" in s
    assert "fallocate" in s
    assert "mkswap" in s
    assert "2G" in s
    assert "serzhan-pool" not in s


def test_persisted_low_swappiness_and_active_gate():
    tasks = yaml.safe_load(TASKS.read_text())
    by_name = {task["name"]: task for task in tasks}
    assert "Activate emergency media swap only if currently inactive" in by_name
    assert "Persist emergency media swap across guest restart" in by_name
    assert by_name["Prefer RAM over guest swap during normal video playback"]["ansible.builtin.copy"]["content"] == "vm.swappiness = 10\n"
    assert "MEDIA_SWAP_NOT_ACTIVE" in str(tasks)
    assert "pri=-5" in str(tasks)
