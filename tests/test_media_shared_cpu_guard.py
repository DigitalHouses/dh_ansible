"""Offline contracts for the persistently managed shared Plex/Jellyfin CPU budget."""
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "roles/media_guest_setup/tasks/cpu_guard.yml"


def test_media_guest_provisioning_imports_the_guard():
    tasks = (ROOT / "roles/media_guest_setup/tasks/main.yml").read_text()
    assert "ansible.builtin.import_tasks: cpu_guard.yml" in tasks
    assert tasks.index("import_tasks: apps.yml") < tasks.index("import_tasks: cpu_guard.yml")


def test_protected_runner_bootstrap_pins_cpu_guard():
    s = (ROOT / "bootstrap/semaphore-host-install.py").read_text()
    assert '"roles/media_guest_setup/tasks/cpu_guard.yml"' in s
    assert '("media_guest_setup", "tasks/cpu_guard.yml")' in s


def test_aggregate_cpu_budget_not_per_player_quota():
    s = POLICY.read_text()
    assert s.count("CPUQuota=100%") == 1
    assert "CPUAccounting=yes" in s
    assert "Slice=dhtranscode.slice" in s
    assert "CPUQuota=150%" not in s
    assert "cpulimit" not in s.lower().replace("pve_cpulimit: '150% is reconciled by protected pve worker'", "")
    assert "plexmediaserver" in s and "jellyfin" in s
    assert "radarr" not in s.lower()
    assert "CPUQuotaPerSecUSec" not in s


def test_idempotent_change_gated_service_restart_and_live_acceptance():
    tasks = yaml.safe_load(POLICY.read_text())
    by_name = {t["name"]: t for t in tasks}
    assert by_name["Restart media players only if their shared CPU policy changed"]["when"] == "dh_media_cpu_slice_file.changed or item.changed"
    assert by_name["Inspect shared cgroup v2 quota"]["ansible.builtin.command"]["argv"] == ["cat", "/sys/fs/cgroup/dhtranscode.slice/cpu.max"]
    assert "100000 100000" in str(tasks)
    assert "MEDIA_TRANSCODE_SHARED_CPU_BUDGET_NOT_APPLIED" in str(tasks)
