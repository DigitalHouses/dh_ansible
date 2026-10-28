"""Safeguard destructive recreate of an explicitly pinned previous managed ZFS pool."""
import importlib.util
import io
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
P = ROOT / "roles/media_guest_setup/files/dh-media-storage.py"
spec = importlib.util.spec_from_file_location("dh_media_openzfs_recreate", P)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

GUID = "17800883176473273026"
NEW_GUID = "1234567890000111"
PHYSICAL = "/dev/sdb"
DISK = "/dev/disk/by-id/wwn-0x5002538d41046527"
PARTS = [
    {"type": "part", "path": "/dev/sdb1",
     "partuuid": "977169b5-b032-3449-8723-01c6eb5cbff0"},
    {"type": "part", "path": "/dev/sdb9",
     "partuuid": "2e5d7d6c-6096-ae40-b1df-b1758705be5e"},
]


def config():
    return {"pool_name": "serzhan-pool", "mode": "readwrite",
            "destructive_without_backup": True,
            "recreate_source_pool":
                {"name": "serzhan-home-pool", "guid": GUID},
            "legacy_pool":
                {"name": "sky_pool", "guid": "814174272168398044",
                 "partition_partuuid": "19c5593e-eb47-4523-b992-45984f5fb2c4"}}


def drive(config_data=None, *, source_guid=GUID, parts=None, policy="erase"):
    data = config_data if config_data is not None else config()
    state = {"old": False, "new": False}
    commands = []
    def pool(name):
        if name == "serzhan-pool":
            return NEW_GUID if state["new"] else None
        if name == "serzhan-home-pool":
            return source_guid if state["old"] else None
        return None
    def imported(name):
        return source_guid if name == "serzhan-home-pool" and not state["old"] else None
    def command(*args, **kwargs):
        commands.append(args)
        if args[:5] == ("/usr/sbin/blkid", "-o", "value", "-s", "TYPE"):
            return "zfs_member" if args[-1] == "/dev/sdb1" else None
        if args[:2] == ("/usr/sbin/zpool", "import"):
            state["old"] = True
        if args[:2] == ("/usr/sbin/zpool", "destroy"):
            assert args[-1] == "serzhan-home-pool"
            state["old"] = False
        if args[:2] == ("/usr/sbin/zpool", "create"):
            state["new"] = True
        return ""
    with patch.object(m, "get_pool", side_effect=pool), \
         patch.object(m, "imported_guid_visible", side_effect=imported), \
         patch.object(m, "physical_partitions", return_value=PARTS if parts is None else parts), \
         patch.object(m, "verify_pool_disk") as verify, \
         patch.object(m, "command", side_effect=command), \
         redirect_stdout(io.StringIO()) as output:
        try:
            result = m.ensure_create(data, DISK, PHYSICAL, policy)
            error = None
        except m.Blocked as exc:
            result = None
            error = str(exc)
    return result, error, commands, verify.call_args_list, output.getvalue()


def writes(commands):
    return [cmd for cmd in commands
            if cmd[:2] in (("/usr/sbin/zpool", "import"),
                           ("/usr/sbin/zpool", "destroy"),
                           ("/usr/sbin/zpool", "create"),
                           ("/usr/sbin/wipefs", "--all"),
                           ("/usr/sbin/sgdisk", "--zap-all"))]


def test_pinned_whole_disk_openzfs_recreate_passes():
    result, error, calls, checked, output = drive()
    assert error is None
    assert result == (NEW_GUID, True)
    assert len(checked) == 2
    assert all(x.args[1] == PHYSICAL for x in checked)
    operations = writes(calls)
    assert [x[1] for x in operations] == [
        "import", "destroy", "--all", "--zap-all", "create"]
    assert "RECREATE_SOURCE_POOL=DESTROYED_EXPLICITLY" in output


def test_wrong_guid_never_imports_or_wipes():
    _, error, calls, _, _ = drive(source_guid="123456789123456789")
    assert error == "RECREATE_SOURCE_GUID_MISMATCH"
    assert writes(calls) == []


@pytest.mark.parametrize("children", [
    [],
    PARTS[:1],
    PARTS + [{"type": "part", "path": "/dev/sdb2",
              "partuuid": "001169b5-b032-3449-8723-01c6eb5cbff0"}],
    [{"type": "part", "path": "/dev/sdc1", "partuuid": PARTS[0]["partuuid"]}, PARTS[1]],
    [PARTS[1], PARTS[0]],
])
def test_unnatural_partition_topology_never_imports_or_wipes(children):
    _, error, calls, _, _ = drive(parts=children)
    assert error == "RECREATE_SOURCE_PARTITION_LAYOUT_MISMATCH"
    assert writes(calls) == []


def test_no_erase_intent_blocks_before_any_write():
    _, error, calls, _, _ = drive(policy="preserve")
    assert error == "RECREATE_SOURCE_ERASE_NOT_AUTHORIZED"
    assert writes(calls) == []


def test_missing_fleet_approval_blocks_before_any_write():
    data = config()
    data["destructive_without_backup"] = False
    _, error, calls, _, _ = drive(config_data=data)
    assert error == "DESTRUCTIVE_APPROVAL_MISSING"
    assert writes(calls) == []


def test_missing_source_declared_does_not_erase_unknown_partitions():
    data = config()
    del data["recreate_source_pool"]
    # Old pool exists, but no source approval exists: data disk must be empty.
    _, error, calls, _, _ = drive(config_data=data)
    assert error == "UNKNOWN_EXISTING_PARTITIONS"
    assert writes(calls) == []
