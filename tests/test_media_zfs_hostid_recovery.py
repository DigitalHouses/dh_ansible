"""ZFS import on a single PCI-selected SATA disk after VM replacement."""
import importlib.util
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "roles/media_guest_setup/files/dh-media-storage.py"
POOL = "serzhan-pool"
DISK = "/dev/sdb"
GUID = "17800883176473273026"


def load():
    spec = importlib.util.spec_from_file_location("media_sata_zfs_import", SRC)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def report(*, pool=POOL, state="ONLINE", hostid=True):
    status = ("status: The pool was last accessed by another system.\n"
              if hostid else "")
    return f"  pool: {pool}\n    id: {GUID}\n state: {state}\n{status}config:\n  {pool} ONLINE\n    sdb1 ONLINE\n"


def import_attempt(*, status=None, access="readwrite"):
    m = load()
    calls = []
    guid_reads = iter([None, GUID])
    def command(*args, **kwargs):
        calls.append(args)
        return ""
    with patch.object(m, "get_pool", side_effect=lambda _: next(guid_reads)), \
         patch.object(m, "importable_report",
                      return_value=report() if status is None else status), \
         patch.object(m, "scan_devices",
                      return_value=["/dev/sdb", "/dev/sdb1"]), \
         patch.object(m, "verify_pool_disk") as verify, \
         patch.object(m, "command", side_effect=command):
        result = m.ensure_import(POOL, DISK, DISK, access)
    return result, calls, verify


def test_previous_vm_hostid_can_force_import_only_online_sata_pool():
    result, calls, verify = import_attempt()
    assert result == (GUID, True)
    assert len(calls) == 1
    argv = calls[0]
    assert argv[:3] == ("/usr/sbin/zpool", "import", "-N")
    assert "-d" in argv and "/dev/sdb1" in argv
    assert "-f" in argv
    assert argv[-1] == POOL
    verify.assert_called_once_with(POOL, DISK)


def test_readonly_import_uses_native_zfs_readonly_property():
    _, calls, _ = import_attempt(access="readonly")
    assert "-o" in calls[0] and "readonly=on" in calls[0]


def test_exported_pool_without_hostid_warning_does_not_force():
    _, calls, _ = import_attempt(status=report(hostid=False))
    assert "-f" not in calls[0]


@pytest.mark.parametrize("untrusted", [
    report(pool="foreign"),
    report(state="DEGRADED"),
    report(state="FAULTED"),
    "",
    "pool: serzhan-pool\nstate: ONLINE\npool: serzhan-pool\nstate: ONLINE\n",
])
def test_missing_ambiguous_or_not_online_pool_never_imported(untrusted):
    m = load()
    with patch.object(m, "get_pool", return_value=None), \
         patch.object(m, "importable_report", return_value=untrusted), \
         patch.object(m, "command", side_effect=AssertionError(
             "Must not execute zpool import")), \
         pytest.raises(m.Blocked, match="REQUESTED_POOL"):
        m.ensure_import(POOL, DISK, DISK, "readwrite")


def test_import_existing_online_pool_without_reimport_or_writes():
    m = load()
    with patch.object(m, "get_pool", return_value=GUID), \
         patch.object(m, "verify_pool_disk") as verify, \
         patch.object(m, "command", side_effect=AssertionError(
             "Unexpected import or disk write")):
        assert m.ensure_import(POOL, DISK, DISK, "readwrite") == (GUID, False)
    verify.assert_called_once_with(POOL, DISK)


def test_import_function_has_no_erase_or_destroy_operations():
    source = SRC.read_text()
    fn = source.split("def ensure_import(", 1)[1].split("def active_pools(", 1)[0]
    for forbidden in ("wipefs", "sgdisk", "destroy", "zpool create"):
        assert forbidden not in fn
