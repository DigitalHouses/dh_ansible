"""Task 80: Semaphore selections are authoritative; no second approval flags."""
import importlib.util
from pathlib import Path
from unittest.mock import patch
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load(relative):
    path = ROOT / relative
    spec = importlib.util.spec_from_file_location(path.stem.replace("-", "_"), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def media():
    return {
        "vmid": 2232,
        "name": "mediaserver-serzhan",
        "os": {"distribution": "debian", "major_version": 13},
        "network": {"bridge": "vmbr0", "ipv4": "192.168.11.232/24"},
        "resources": {
            "cores": 2, "memory_mb": 4096, "disk_gb": 64,
            "cpu_limit": 1.5, "cpu_units": 50, "ballooning": False,
            "zfs_arc_max_mb": 768, "transcode_cpu_limit": 1.0,
        },
        "pci": {"gpu": "0000:2f:00.0", "sata_controller": "0000:2b:00.0"},
        # No approvals.* fields: survey is the authorization.
    }


@pytest.mark.parametrize("vm_action", ["create", "keep", "recreate"])
@pytest.mark.parametrize("disk_action", ["import", "erase"])
def test_hardware_preflight_accepts_survey_actions_without_approvals(
    vm_action, disk_action, capsys
):
    m = load("scripts/dh-media-pve-preflight.py")
    data = media()
    args = [
        "dh-media-pve-preflight", "--site", "serzhan-home",
        "--vm-action", vm_action, "--disk-action", disk_action,
    ]
    with (
        patch.object(sys, "argv", args),
        patch.object(m, "inventory", return_value=data),
        patch.object(m, "verify_controller", return_value="0000:2b:00.0"),
        patch.object(m, "verify_vm", return_value="MANAGED_VM") as verify_vm,
    ):
        m.main()
    verify_vm.assert_called_once_with(data, vm_action)
    assert "MEDIA_PVE_PREFLIGHT=PASS" in capsys.readouterr().out


def test_pve_provision_contract_does_not_require_manifest_approval_flags():
    m = load("bootstrap/dh-pve-media-deploy.py")
    data = media()
    # Simulate present PCI devices, absent target VM, and no existing PCI owners.
    with (
        patch.object(Path, "exists", autospec=True,
                     side_effect=lambda path: str(path).startswith(
                         "/sys/bus/pci/devices/")),
        patch.object(Path, "glob", return_value=[]),
        patch.object(m, "run", return_value="local-lvm active"),
    ):
        assert m.check("serzhan-home", data, {"host": "192.168.11.6"}) == (
            2232, "mediaserver-serzhan", "192.168.11.232/24"
        )
