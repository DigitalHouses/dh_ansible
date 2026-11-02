"""NTFS -> canonical ZFS: physical identity, exact partitions, no repeat wipe."""
from __future__ import annotations
import importlib.util
import json
from pathlib import Path
from unittest.mock import patch

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "roles/media_guest_setup/files/dh-media-storage.py"


def module():
    spec = importlib.util.spec_from_file_location("dh_one_time_ntfs_test", SOURCE)
    obj = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obj)
    return obj


def approved_data():
    return {
        "pool_name": "serzhan-pool", "mode": "readwrite",
        "destructive_without_backup": True,
        "disk": {
            "serial": "PK1331PAGH7VHS", "wwn": "0x5000cca22bc6ef0d",
            "size_bytes": 4000787030016,
            "observed_filesystem": "ntfs", "observed_label": "HITACHI_4TB",
            "observed_partuuid": "7666ad6d-c11f-4292-914f-ea7eef4d420c",
            "observed_reserved_partuuid": "225fc0f3-0a5c-4b88-98d2-06c946051f69",
        },
        "one_time_ntfs_erase": {
            "approved": True, "scope": "first_create_only",
            "serial": "PK1331PAGH7VHS", "wwn": "0x5000cca22bc6ef0d",
            "size_bytes": 4000787030016,
            "partition_filesystem": "ntfs", "partition_label": "HITACHI_4TB",
            "partition_partuuid": "7666ad6d-c11f-4292-914f-ea7eef4d420c",
            "reserved_partuuid": "225fc0f3-0a5c-4b88-98d2-06c946051f69",
        },
    }


def partitions():
    d = approved_data()
    return [
        {"path": "/dev/sdb1", "partuuid": d["disk"]["observed_reserved_partuuid"], "type": "part"},
        {"path": "/dev/sdb2", "partuuid": d["disk"]["observed_partuuid"], "type": "part"},
    ]


def command(*args, **kwargs):
    if "/usr/sbin/blkid" in args[0]:
        path = args[-1]
        field = args[args.index("-s") + 1]
        if path == "/dev/sdb1" and field == "TYPE":
            return None
        if path == "/dev/sdb2" and field == "TYPE":
            return "ntfs"
        if path == "/dev/sdb2" and field == "LABEL":
            return "HITACHI_4TB"
    if args[0] == "/usr/bin/findmnt":
        return None
    raise AssertionError("Unexpected command " + str(args))


def test_exact_approved_ntfs_creates_root_only_resume_marker(tmp_path):
    m = module()
    m.NTFS_ERASE_MARKER = tmp_path / "marker.json"
    with patch.object(m, "physical_partitions", return_value=partitions()), \
         patch.object(m, "command", side_effect=command):
        m.verify_one_time_ntfs_erase(approved_data(), "/dev/sdb")
    marker = m.NTFS_ERASE_MARKER
    assert marker.exists() and marker.stat().st_mode & 0o777 == 0o600
    saved = json.loads(marker.read_text())
    assert saved["wwn"] == approved_data()["disk"]["wwn"]
    assert saved["partuuid"] == approved_data()["disk"]["observed_partuuid"]


@pytest.mark.parametrize("field,value", [
    ("wwn", "0x5000cca22bc6ef0e"),
    ("serial", "SOMEOTHERDISK"),
    ("size_bytes", 1000204886016),
    ("partition_partuuid", "01234567-89ab-cdef-0123-456789abcdef"),
    ("partition_label", "ANOTHER_LABEL"),
    ("partition_filesystem", "ext4"),
    ("approved", False),
    ("scope", "any_disk"),
])
def test_mismatched_scope_blocks_before_any_write(tmp_path, field, value):
    m = module()
    m.NTFS_ERASE_MARKER = tmp_path / "marker"
    data = approved_data()
    data["one_time_ntfs_erase"][field] = value
    with patch.object(m, "physical_partitions", side_effect=AssertionError("partition queried")):
        with pytest.raises(m.Blocked, match="ONE_TIME_NTFS"):
            m.verify_one_time_ntfs_erase(data, "/dev/sdb")
    assert not m.NTFS_ERASE_MARKER.exists()


def test_unapproved_foreign_partition_still_blocks(tmp_path):
    m = module()
    data = approved_data()
    data.pop("one_time_ntfs_erase")
    with patch.object(m, "get_pool", return_value=None), \
         patch.object(m, "imported_guid_visible", return_value=None), \
         patch.object(m, "physical_partitions", return_value=partitions()), \
         patch.object(m, "command", side_effect=AssertionError("should not wipe")):
        with pytest.raises(m.Blocked, match="UNKNOWN_EXISTING_PARTITIONS"):
            m.ensure_create(data, "/dev/disk/by-id/wwn-0x5000cca22bc6ef0d", "/dev/sdb", "erase")


def test_canonical_pool_is_preserved_even_with_erase_survey(tmp_path):
    m = module()
    data = approved_data()
    with patch.object(m, "get_pool", return_value="123"), \
         patch.object(m, "ensure_import", return_value=("123", False)) as verified, \
         patch.object(m, "command", side_effect=AssertionError("must not wipe")):
        assert m.ensure_create(data, "/dev/disk/by-id/wwn-0x5000cca22bc6ef0d",
                               "/dev/sdb", "erase") == ("123", False)
        verified.assert_called_once()


def test_wrong_partuuid_never_creates_marker(tmp_path):
    m = module()
    m.NTFS_ERASE_MARKER = tmp_path / "marker"
    bad = partitions()
    bad[1]["partuuid"] = "00000000-0000-0000-0000-000000000000"
    with patch.object(m, "physical_partitions", return_value=bad), \
         patch.object(m, "command", side_effect=AssertionError("no signature probes")):
        with pytest.raises(m.Blocked, match="NTFS_APPROVED_PARTUUID_MISMATCH"):
            m.verify_one_time_ntfs_erase(approved_data(), "/dev/sdb")
    assert not m.NTFS_ERASE_MARKER.exists()


def test_ntfs_is_never_erased_when_mounted(tmp_path):
    m = module()
    m.NTFS_ERASE_MARKER = tmp_path / "marker"
    def mounted(*args, **kwargs):
        if args[0] == "/usr/bin/findmnt" and args[-1] == "/dev/sdb2":
            return "/dev/sdb2 /media/foreign ntfs rw"
        return command(*args, **kwargs)
    with patch.object(m, "physical_partitions", return_value=partitions()), \
         patch.object(m, "command", side_effect=mounted):
        with pytest.raises(m.Blocked, match="NTFS_DISK_IN_USE_MOUNTED"):
            m.verify_one_time_ntfs_erase(approved_data(), "/dev/sdb")
    assert not m.NTFS_ERASE_MARKER.exists()
