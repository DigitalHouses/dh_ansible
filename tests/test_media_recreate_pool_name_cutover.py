"""Managed media recreate with explicit previous-pool name and GUID."""
import importlib.util
import json
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "dh_media_pool_cutover", ROOT / "bootstrap/dh-pve-media-deploy.py")
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)

OLD_GUID = "17800883176473273026"
NEW_GUID = "9999999999999999999"


def data(source=True):
    result = {
        "name": "mediaserver-demo-home",
        "pci": {"gpu": "0000:00:02.0",
                "sata_controller": "0000:00:17.0"},
        "storage": {
            "pool_name": "demo-pool",
            "disk": {"serial": "S2PWNX0H603177N",
                     "wwn": "0x5002538d41046527",
                     "size_bytes": 1000204886016},
        },
    }
    if source:
        result["storage"]["recreate_source_pool"] = {
            "name": "demo-home-pool", "guid": OLD_GUID}
    return result


def vm_state():
    return {
        "name": "mediaserver-demo-home",
        "description": m.MANAGED_MEDIA_DESCRIPTION,
        "scsi0": "local-lvm:vm-2238-disk-0,size=64G",
        "hostpci0": "0000:00:02.0,pcie=1",
        "hostpci1": "0000:00:17.0,pcie=1",
    }


def guest(inventory):
    def answer(_vmid, *args):
        if args[0].endswith("/lsblk") and "-J" in args:
            return json.dumps({"blockdevices": [
                {"name": "sdb", "path": "/dev/sdb", "type": "disk",
                 "serial": "S2PWNX0H603177N",
                 "wwn": "0x5002538d41046527",
                 "size": 1000204886016}]})
        if args[0].endswith("/zpool") and "list" in args:
            return inventory
        if args[0].endswith("/zpool") and "get" in args:
            return OLD_GUID if args[-1] == "demo-home-pool" else NEW_GUID
        if args[0].endswith("/zpool") and "status" in args:
            return "config:\n    /dev/sdb1 ONLINE 0 0 0"
        if args[0].endswith("/lsblk") and "-n" in args:
            return "sdb"
        raise AssertionError("Unexpected guest exec route")
    return answer


def check(info, inventory):
    with patch.object(m, "config", return_value=vm_state()), \
         patch.object(m, "guest_exec", side_effect=guest(inventory)), \
         patch.object(m, "qm", side_effect=AssertionError("Must be read-only")):
        return m.old_guest_matches(2238, "demo-home", info, managed=True)


def test_explicit_source_name_and_guid_passes_before_recreate():
    assert check(data(), "demo-home-pool\t" + OLD_GUID) is None


def test_already_commissioned_new_name_passes_repeat_recreate():
    assert check(data(), "demo-pool\t" + NEW_GUID) is None


def test_changed_guid_blocks_before_any_vm_mutation():
    try:
        check(data(), "demo-home-pool\t12345678901234567")
        assert False
    except m.Blocked as e:
        assert str(e) == "MANAGED_ZFS_SOURCE_NOT_APPROVED"


def test_unknown_source_blocks_before_any_vm_mutation():
    try:
        check(data(source=False), "demo-home-pool\t" + OLD_GUID)
        assert False
    except m.Blocked as e:
        assert str(e) == "MANAGED_ZFS_SOURCE_NOT_APPROVED"


def test_unrecognized_managed_pool_name_blocks():
    try:
        check(data(), "foreign-pool\t" + OLD_GUID)
        assert False
    except m.Blocked as e:
        assert str(e) == "MANAGED_ZFS_SOURCE_NOT_APPROVED"
