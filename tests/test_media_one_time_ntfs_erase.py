"""Regression: explicit erase accepts NTFS, ZFS or arbitrary old partitions.

The old one-time Hitachi NTFS/WWN/PARTUUID contract is intentionally retired.
All tests are offline: no disk commands are executed.
"""
import importlib.util
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
P = ROOT / "roles/media_guest_setup/files/dh-media-storage.py"


def load():
    spec = importlib.util.spec_from_file_location("media_sata_erase", P)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def config():
    return {"pool_name": "serzhan-pool",
            "destructive_without_backup": True}


@pytest.mark.parametrize("old_partitions", [
    ["/dev/sdb"],
    ["/dev/sdb", "/dev/sdb1", "/dev/sdb2"],
    ["/dev/sdb", "/dev/sdb1", "/dev/sdb9"],
    ["/dev/sdb", "/dev/sdb1", "/dev/sdb2", "/dev/sdb3"],
])
def test_erase_ignores_previous_partition_format_and_guid(old_partitions):
    m = load()
    calls = []
    def fake_command(*args, **kwargs):
        calls.append(args)
        return None if args[0].endswith("findmnt") else ""
    with patch.object(m, "active_pools", return_value=[]), \
         patch.object(m, "scan_devices", return_value=old_partitions), \
         patch.object(m, "get_pool", return_value="123456"), \
         patch.object(m, "verify_pool_disk") as verified, \
         patch.object(m, "command", side_effect=fake_command):
        assert m.erase_disk(config(), "/dev/sdb") == ("123456", True)
    assert [args[2] for args in calls
            if args[:2] == ("/usr/sbin/wipefs", "--all")] == list(
                reversed(old_partitions))
    assert ("/usr/sbin/sgdisk", "--zap-all", "/dev/sdb") in calls
    assert any(args[:2] == ("/usr/sbin/zpool", "create") for args in calls)
    verified.assert_called_once_with("serzhan-pool", "/dev/sdb")


def test_erase_destroys_only_an_active_pool_on_the_selected_sata_disk():
    m = load()
    calls = []
    def command(*args, **kwargs):
        calls.append(args)
        return ""
    with patch.object(m, "active_pools", return_value=["old-pool", "unrelated"]), \
         patch.object(m, "pool_leaves", side_effect=lambda name:
                      ["/dev/sdb"] if name == "old-pool" else ["/dev/vda"]), \
         patch.object(m, "scan_devices", return_value=["/dev/sdb", "/dev/sdb1"]), \
         patch.object(m, "get_pool", return_value="111"), \
         patch.object(m, "verify_pool_disk"), \
         patch.object(m, "command", side_effect=command):
        m.erase_disk(config(), "/dev/sdb")
    assert ("/usr/sbin/zpool", "destroy", "-f", "old-pool") in calls
    assert not any("unrelated" in args for args in calls)


def test_erase_cannot_destroy_multivdev_pool():
    m = load()
    with patch.object(m, "active_pools", return_value=["old-pool"]), \
         patch.object(m, "pool_leaves", return_value=["/dev/sdb", "/dev/sdc"]), \
         patch.object(m, "command", side_effect=AssertionError("disk write")):
        with pytest.raises(m.Blocked, match="SATA_DISK_PART_OF_MULTI_VDEV_POOL"):
            m.erase_disk(config(), "/dev/sdb")


def test_erase_refuses_same_pool_name_active_on_another_disk():
    m = load()
    with patch.object(m, "active_pools", return_value=["serzhan-pool"]), \
         patch.object(m, "pool_leaves", return_value=["/dev/vda"]), \
         patch.object(m, "command", side_effect=AssertionError("disk write")):
        with pytest.raises(m.Blocked, match="TARGET_POOL_NAME_ACTIVE_ON_OTHER_DISK"):
            m.erase_disk(config(), "/dev/sdb")


def test_erase_requires_explicit_policy_without_any_write():
    m = load()
    bad = config()
    bad["destructive_without_backup"] = False
    with patch.object(m, "active_pools", side_effect=AssertionError("disk inspected")), \
         patch.object(m, "command", side_effect=AssertionError("disk write")):
        with pytest.raises(m.Blocked, match="DISK_ERASE_NOT_APPROVED"):
            m.erase_disk(bad, "/dev/sdb")


def test_erase_never_wipes_an_actively_mounted_partition():
    m = load()
    calls = []
    def command(*args, **kwargs):
        calls.append(args)
        if args[0].endswith("findmnt"):
            return "mounted"
        return ""
    with patch.object(m, "active_pools", return_value=[]), \
         patch.object(m, "scan_devices", return_value=["/dev/sdb", "/dev/sdb1"]), \
         patch.object(m, "command", side_effect=command):
        with pytest.raises(m.Blocked, match="SATA_DISK_BUSY_MOUNTED"):
            m.erase_disk(config(), "/dev/sdb")
    assert not any(args[0].endswith(("wipefs", "sgdisk")) for args in calls)


def test_erase_path_does_not_require_old_wwn_serial_ntfs_or_pool_guid():
    source = P.read_text()
    function = source.split("def erase_disk(", 1)[1].split("def validate_layout(", 1)[0]
    for forbidden in ("wwn", "serial", "partuuid", "ntfs",
                      "import_pool_guid", "recreate_source_pool"):
        assert forbidden not in function.lower()


def test_boot_importer_only_runs_import():
    text = (ROOT / "roles/media_guest_setup/templates/dh-media-zfs-mount.sh.j2").read_text()
    assert "--action import" in text
    assert "--action erase" not in text
    assert all(x not in text for x in ("wipefs", "sgdisk", "zpool destroy", "zpool create"))
