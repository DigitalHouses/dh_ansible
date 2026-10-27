"""Read-only offline acceptance of media create/import boundaries."""
from pathlib import Path
import importlib.util
from unittest.mock import patch
import pytest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "roles/media_guest_setup/files/dh-media-storage.py"

def load():
    spec = importlib.util.spec_from_file_location("dh_media_storage", SOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def test_repeat_create_never_wipes_canonical_pool():
    m = load()
    data = {"pool_name": "serzhan-home-pool", "mode": "readwrite"}
    with patch.object(m, "get_pool", return_value="123"), \
         patch.object(m, "ensure_import", return_value=("123", False)), \
         patch.object(m, "command", side_effect=AssertionError("unexpected disk operation")):
        assert m.ensure_create(data, "/dev/disk/by-id/wwn-example", "/dev/sdb") == ("123", False)

def test_create_rejects_unrecognized_legacy_guid():
    m = load()
    data = {"pool_name": "serzhan-home-pool",
            "legacy_pool": {"name": "sky_pool", "guid": "814174272168398044"},
            "destructive_without_backup": True}
    with patch.object(m, "get_pool", side_effect=lambda name: "000" if name == "sky_pool" else None), \
         patch.object(m, "imported_guid_visible", return_value=None), \
         patch.object(m, "command", side_effect=AssertionError("unexpected disk operation")):
        with pytest.raises(m.Blocked, match="LEGACY_POOL_GUID_MISMATCH"):
            m.ensure_create(data, "/dev/disk/by-id/wwn-example", "/dev/sdb", "erase")

def test_import_handler_contains_no_wipe():
    import inspect
    m = load()
    source = inspect.getsource(m.ensure_import)
    for forbidden in ("wipefs", "sgdisk", "destroy", "zpool create"):
        assert forbidden not in source

def test_vm_recreation_checks_exact_wwn_serial_guid_before_destroy():
    text = (ROOT / "bootstrap/dh-pve-media-deploy.py").read_text()
    for phrase in ("old_guest_matches(vmid, site, data, managed=managed)",
                   "LEGACY_PHYSICAL_DISK_IDENTITY_MISMATCH",
                   "LEGACY_ZFS_GUID_MISMATCH", "UNEXPECTED_VM_DISK_"):
        assert phrase in text
