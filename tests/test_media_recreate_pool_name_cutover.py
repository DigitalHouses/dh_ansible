"""Managed VM recreate keeps strict VM identity, not old ZFS/WWN history."""
import importlib.util
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "media_managed_recreate", ROOT / "bootstrap/dh-pve-media-deploy.py")
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)


def config():
    return {
        "name": "mediaserver-demo-home",
        "pci": {"gpu": "0000:00:02.0", "sata_controller": "0000:00:17.0"},
        "storage": {"pool_name": "demo-pool",
                    "import_pool_guid": "old-value-is-irrelevant-to-erase"},
    }


def state():
    return {
        "name": "mediaserver-demo-home",
        "description": m.MANAGED_MEDIA_DESCRIPTION,
        "scsi0": "local-lvm:vm-2238-disk-0,size=64G",
        "hostpci0": "0000:00:02.0,pcie=1",
        "hostpci1": "0000:00:17.0,pcie=1",
    }


def check(original, vm=None, managed=True):
    with patch.object(m, "config", return_value=original), \
         patch.object(m, "guest_exec", side_effect=AssertionError(
             "PVE must not inspect guest ZFS or WWN before VM recreate")):
        return m.old_guest_matches(2238, "demo-home",
                                   config() if vm is None else vm,
                                   managed=managed)


def test_managed_vm_identity_passes_without_original_pool_guid_or_serial():
    assert check(state()) is None


def test_legacy_or_foreign_vm_is_never_destroyed_even_with_erase():
    with pytest.raises(m.Blocked, match="FOREIGN_VM_BLOCKED"):
        check(state(), managed=False)
    bad = state()
    bad["name"] = "foreign-media"
    with pytest.raises(m.Blocked, match="FOREIGN_VM_BLOCKED"):
        check(bad)


@pytest.mark.parametrize("field,value,reason", [
    ("hostpci1", "0000:01:00.0,pcie=1", "SATA_CONFIG_DRIFT"),
    ("hostpci0", "0000:02:00.0,pcie=1", "GPU_CONFIG_DRIFT"),
    ("scsi0", "local-lvm:vm-2253-disk-0,size=64G", "FOREIGN_BOOT_VOLUME"),
    ("lock", "backup", "VM_LOCKED"),
    ("scsi1", "local-lvm:vm-2238-disk-1", "UNEXPECTED_VM_DISK_scsi1"),
])
def test_existing_managed_vm_recreate_rejects_foreign_resources(field,value,reason):
    bad = state()
    bad[field] = value
    with pytest.raises(m.Blocked, match=reason):
        check(bad)


def test_vm_recreate_logic_never_uses_obsolete_zfs_erase_preconditions():
    code = (ROOT / "bootstrap/dh-pve-media-deploy.py").read_text()
    gate = code.split("def old_guest_matches(", 1)[1].split(
        "def reconcile_media_vm_lifecycle(", 1)[0]
    for forbidden in ("import_pool_guid", "recreate_source_pool",
                      "serial", "wwn", "zpool"):
        assert forbidden not in gate.lower()
