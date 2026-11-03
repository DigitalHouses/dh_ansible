"""Retired ZFS-partition-pinning contract: test actual sole SATA disk topology."""
import importlib.util
import json
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "media_sata_partition_contract",
    ROOT / "roles/media_guest_setup/files/dh-media-storage.py")
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)


@pytest.mark.parametrize("parts", [
    [],
    [{"path": "/dev/sdb1", "type": "part"}],
    [{"path": "/dev/sdb1", "type": "part"}, {"path": "/dev/sdb9", "type": "part"}],
    [{"path": "/dev/sdb1", "type": "part"}, {"path": "/dev/sdb2", "type": "part"},
     {"path": "/dev/sdb3", "type": "part"}],
])
def test_old_partition_shapes_are_all_scanned_on_one_sata_disk(parts):
    def fake_command(*args, **kwargs):
        assert args[:2] == ("/usr/bin/lsblk", "-J")
        return json.dumps({"blockdevices": [
            {"path": "/dev/sdb", "type": "disk", "children": parts}]})
    with patch.object(m, "command", side_effect=fake_command):
        assert m.scan_devices("/dev/sdb") == [
            "/dev/sdb", *[part["path"] for part in parts]]


@pytest.mark.parametrize("root", [
    {"path": "/dev/sdc", "type": "disk", "children": []},
    {"path": "/dev/sdb", "type": "part", "children": []},
    {"path": "/dev/sdb", "type": "disk", "children": [
        {"path": "/dev/sdc1", "type": "part"}]},
    {"path": "/dev/sdb", "type": "disk", "children": [
        {"path": "/dev/sdb1", "type": "lvm"}]},
    {"path": "/dev/sdb", "type": "disk", "children": [
        {"path": "/dev/sdb1", "type": "part", "children": [{"type": "crypt"}]}]},
])
def test_unexpected_or_foreign_device_tree_blocks_before_writes(root):
    with patch.object(m, "command", return_value=json.dumps({"blockdevices": [root]})):
        with pytest.raises(m.Blocked, match="SATA_DISK_.*TOPOLOGY_INVALID"):
            m.scan_devices("/dev/sdb")


def test_real_zfs_partition_resolves_to_its_sata_parent():
    with patch.object(m, "command", return_value="sdb1 part\nsdb disk"):
        assert m.leaf_disk("/dev/sdb1") == "/dev/sdb"


def test_zfs_pool_must_have_only_one_leaf_on_selected_sata_disk():
    with patch.object(m, "pool_leaves", return_value=["/dev/sdb"]):
        m.verify_pool_disk("serzhan-pool", "/dev/sdb")
    with patch.object(m, "pool_leaves", return_value=["/dev/sdb", "/dev/sdc"]):
        with pytest.raises(m.Blocked, match="ZFS_VDEV_NOT_THE_SOLE_SATA_DISK"):
            m.verify_pool_disk("serzhan-pool", "/dev/sdb")


def test_pool_on_another_disk_is_never_accepted():
    with patch.object(m, "pool_leaves", return_value=["/dev/vda"]):
        with pytest.raises(m.Blocked, match="ZFS_VDEV_NOT_THE_SOLE_SATA_DISK"):
            m.verify_pool_disk("serzhan-pool", "/dev/sdb")
