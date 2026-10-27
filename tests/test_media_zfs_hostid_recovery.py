"""Fail-closed tests for reassignment of an existing ZFS disk to a new VM."""
import importlib.util
import io
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "roles/media_guest_setup/files/dh-media-storage.py"
POOL = "serzhan-home-pool"
DISK = "/dev/disk/by-id/wwn-0x5002538d41046527"
PHYSICAL = "/dev/sdb"
GUID = "17800883176473273026"


def load():
    spec = importlib.util.spec_from_file_location("dh_media_storage", SOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def report(*, disk="wwn-0x5002538d41046527", state="ONLINE",
           status="The pool was last accessed by another system.",
           extra=""):
    return (
        f"  pool: {POOL}\n"
        f"    id: {GUID}\n"
        f" state: {state}\n"
        f"status: {status}\n"
        "config:\n"
        f"\t{POOL}       ONLINE\n"
        f"\t  {disk}  ONLINE\n"
        f"{extra}"
    )


def test_exact_single_wwn_previous_vm_may_be_force_imported():
    m = load()
    invoked = []
    pool_reads = iter((None, GUID))

    def cmd(*args, **kwargs):
        invoked.append(args)
        if args[1:3] == ("import", "-N") and not kwargs.get("allow_failure"):
            return ""
        if args[1:3] == ("import", "-N"):
            return None
        if args[1:3] == ("import", "-d"):
            return report()
        raise AssertionError(str(args))

    with (patch.object(m, "get_pool", side_effect=lambda n: next(pool_reads)),
          patch.object(m, "imported_guid_visible", return_value=GUID),
          patch.object(m, "verify_pool_disk") as verified,
          patch.object(m, "command", side_effect=cmd)):
        with redirect_stdout(io.StringIO()) as log:
            assert m.ensure_import(POOL, DISK, PHYSICAL, "readwrite") == (GUID, True)
    verified.assert_called_once_with(POOL, PHYSICAL)
    assert any("-f" in args for args in invoked)
    assert "POOL=FORCE_IMPORTED_AFTER_VERIFIED_VM_REPLACEMENT" in log.getvalue()


@pytest.mark.parametrize("evidence", [
    report(disk="wwn-0x1111111111111111"),
    report(extra="\t  wwn-0x2222222222222222  ONLINE\n"),
    report(state="DEGRADED"),
    report(status="This pool is currently active."),
    "  pool: foreign\n    id: 123\n",
    "",
])
def test_no_force_import_without_all_valid_evidence(evidence):
    m = load()
    with patch.object(m, "command", return_value=evidence):
        with pytest.raises(m.Blocked, match="FORCE_IMPORT_"):
            m.allow_force_import_of_sole_verified_vdev(POOL, DISK)


def test_guard_import_read_only_mode_keeps_readonly_parameter():
    m = load()
    calls = []
    pool_reads = iter((None, GUID))

    def cmd(*args, **kwargs):
        calls.append(args)
        if args[1:3] == ("import", "-N") and not kwargs.get("allow_failure"):
            return ""
        if args[1:3] == ("import", "-N"):
            return None
        if args[1:3] == ("import", "-d"):
            return report()
        raise AssertionError(str(args))

    with (patch.object(m, "get_pool", side_effect=lambda _: next(pool_reads)),
          patch.object(m, "imported_guid_visible", return_value=GUID),
          patch.object(m, "verify_pool_disk"),
          patch.object(m, "command", side_effect=cmd)):
        with redirect_stdout(io.StringIO()):
            assert m.ensure_import(POOL, DISK, PHYSICAL, "readonly") == (GUID, True)
    forced = next(args for args in calls if "-f" in args)
    assert "readonly=on" in forced


def test_no_zpool_write_if_reported_vdev_is_foreign():
    m = load()
    calls = []

    def cmd(*args, **kwargs):
        calls.append(args)
        if args[1:3] == ("import", "-N"):
            return None
        if args[1:3] == ("import", "-d"):
            return report(disk="wwn-0x1111111111111111")
        raise AssertionError(str(args))

    with (patch.object(m, "get_pool", return_value=None),
          patch.object(m, "imported_guid_visible", return_value=GUID),
          patch.object(m, "command", side_effect=cmd)):
        with pytest.raises(m.Blocked, match="FORCE_IMPORT_VDEV"):
            m.ensure_import(POOL, DISK, PHYSICAL, "readwrite")
    assert not any("-f" in args for args in calls)
