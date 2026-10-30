"""Plex Agent GPU PMU permission and fresh metrics are required on media guests."""
from pathlib import Path
import yaml


ROOT = Path(__file__).resolve().parents[1]
ROLE = ROOT / "roles/media_guest_setup/tasks/plex_agent.yml"


def test_gpu_helper_uses_least_privilege_perfmon_override():
    tasks = yaml.safe_load(ROLE.read_text())
    by_name = {t["name"]: t for t in tasks}
    task = by_name["Grant only PMU performance monitoring capability to Plex GPU helper"]
    copy = task["ansible.builtin.copy"]
    assert copy["dest"].endswith("digitalhouses_plex_agent_gpu_helper.service.d/20-dh-intel-pmu.conf")
    assert "CapabilityBoundingSet=CAP_PERFMON" in copy["content"]
    assert "AmbientCapabilities=CAP_PERFMON" in copy["content"]
    assert "CapabilityBoundingSet=CAP_SYS_ADMIN" not in copy["content"]
    assert "AmbientCapabilities=CAP_SYS_ADMIN" not in copy["content"]


def test_gpu_helper_reload_restart_and_fresh_gpu_acceptance():
    tasks = yaml.safe_load(ROLE.read_text())
    names = [t["name"] for t in tasks]
    a = names.index("Grant only PMU performance monitoring capability to Plex GPU helper")
    b = names.index("Reload systemd unit configuration after GPU helper override changes")
    c = names.index("Start Plex GPU helper with the corrected minimal PMU capability")
    e = names.index("Wait for a fresh, usable Intel GPU PMU sample from the helper")
    assert a < b < c < e
    check = tasks[e]["ansible.builtin.command"]
    assert "collected_at_epoch" in " ".join(str(x) for x in check["argv"])
    assert tasks[e]["retries"] >= 4
    assert "changed_when" in tasks[e]


def test_media_role_pins_plex_agent_0_8_5_or_later_stable_release():
    source = ROLE.read_text()
    assert "digitalhouses_plex_agent-v0.8.5" in source
    assert "digitalhouses_plex_agent-v0.8.0" not in source
    assert "DIGITALHOUSES_SOURCE_REF: digitalhouses_plex_agent-v0.8.5" in source
