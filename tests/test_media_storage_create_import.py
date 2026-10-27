"""No-hardware safety acceptance for media storage create/import lifecycle."""
from __future__ import annotations

import importlib.util
import io
import json
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch
import unittest

ROOT = Path(__file__).resolve().parents[1]


def load(path):
    spec = importlib.util.spec_from_file_location(path.stem.replace("-", "_"), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def configuration():
    return {
        "pool_name": "demo-home-pool",
        "media_dataset": "demo-home-pool/media",
        "mode": "readwrite",
        "disk": {"serial": "ABCDEFGHI123", "wwn": "0x1234567890abcdef",
                 "size_bytes": 1000204886016},
        "legacy_pool": {"name": "old_pool", "guid": "123456789012345",
                        "partition_partuuid": "f1200000-aaaa-4444-aaaa-000000000000"},
        "destructive_without_backup": True,
        "layout": {"datasets": ["media", "doc", "backups"],
                   "directories": ["media/movies", "media/music", "backups/ha"]},
    }


class StorageLifecycle(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.m = load(ROOT / "roles/media_guest_setup/files/dh-media-storage.py")

    def test_good_layout(self):
        self.m.validate_layout(configuration())

    def test_unknown_dataset_rejected(self):
        data = configuration()
        data["layout"]["directories"].append("foreign/data")
        with self.assertRaisesRegex(self.m.Blocked, "UNDECLARED_DATASET"):
            self.m.validate_layout(data)

    def test_path_escape_rejected(self):
        data = configuration()
        data["layout"]["directories"].append("media/../../etc")
        with self.assertRaisesRegex(self.m.Blocked, "INVALID_DIRECTORY_PATH"):
            self.m.validate_layout(data)

    def test_malformed_layout_rejected_before_wipe(self):
        m = self.m
        data = configuration()
        data["layout"]["directories"] = ["bad/.."]
        with patch.object(m, "find_disk", side_effect=AssertionError("disk touched")):
            with self.assertRaises(m.Blocked):
                m.validate_layout(data)

    def test_wrong_legacy_guid_blocks_destructive_commands(self):
        m = self.m
        data = configuration()
        with patch.object(m, "get_pool", side_effect=lambda name: None if name == "demo-home-pool" else "999999"), \
             patch.object(m, "imported_guid_visible", return_value=None), \
             patch.object(m, "command", side_effect=AssertionError("write attempted")):
            with self.assertRaisesRegex(m.Blocked, "LEGACY_POOL_GUID_MISMATCH"):
                m.ensure_create(data, "/dev/disk/by-id/wwn-0x1234567890abcdef", "/dev/sdb", "erase")

    def test_unknown_existing_partitions_fail_closed(self):
        m = self.m
        data = configuration()
        def command(*args, **kwargs):
            if args[:2] == ("/usr/bin/lsblk", "-J"):
                return json.dumps({"blockdevices": [{"children": [{"type": "part"}]}]})
            raise AssertionError("unexpected device write or command: " + str(args))
        with patch.object(m, "get_pool", return_value=None), \
             patch.object(m, "imported_guid_visible", return_value=None), \
             patch.object(m, "command", side_effect=command):
            with self.assertRaisesRegex(m.Blocked, "UNKNOWN_EXISTING_PARTITIONS"):
                m.ensure_create(data, "/dev/disk/by-id/wwn-0x1234567890abcdef", "/dev/sdb")


    def test_existing_legacy_pool_is_preserved_without_erase(self):
        m = self.m
        data = configuration()
        with (patch.object(m, "get_pool", side_effect=lambda name:
                           None if name == "demo-home-pool" else "123456789012345"),
              patch.object(m, "imported_guid_visible", return_value=None),
              patch.object(m, "command", side_effect=AssertionError("write attempted"))):
            with self.assertRaisesRegex(m.Blocked, "LEGACY_POOL_ERASE_NOT_AUTHORIZED"):
                m.ensure_create(data, "/dev/disk/by-id/verified", "/dev/sdb", "preserve")

    def test_canonical_pool_erase_needs_fleet_permission(self):
        m = self.m
        data = configuration()
        data["destructive_without_backup"] = False
        with (patch.object(m, "get_pool", return_value="445566778899"),
              patch.object(m, "ensure_import", return_value=("445566778899", False)),
              patch.object(m, "command", side_effect=AssertionError("disk write attempted"))):
            with self.assertRaisesRegex(m.Blocked, "DESTRUCTIVE_APPROVAL_MISSING"):
                m.ensure_create(data, "/dev/disk/by-id/verified", "/dev/sdb", "erase")

    def test_import_has_no_create_wipe_or_destroy(self):
        m = self.m
        with patch.object(m, "get_pool", return_value="445566778899"), \
             patch.object(m, "verify_pool_disk") as verify, \
             patch.object(m, "command", side_effect=AssertionError("unexpected write")):
            self.assertEqual(
                m.ensure_import("demo-home-pool", "/dev/disk/by-id/wwn-0x1234567890abcdef",
                                "/dev/sdb", "readwrite"),
                ("445566778899", False))
            verify.assert_called_once_with("demo-home-pool", "/dev/sdb")

    def test_canonical_create_is_repeatable_without_wipe(self):
        m = self.m
        with patch.object(m, "get_pool", return_value="445566778899"), \
             patch.object(m, "ensure_import", return_value=("445566778899", False)) as imp, \
             patch.object(m, "command", side_effect=AssertionError("unexpected write")):
            with redirect_stdout(io.StringIO()):
                result = m.ensure_create(configuration(), "/dev/disk/by-id/example", "/dev/sdb")
            self.assertEqual(result, ("445566778899", False))
            imp.assert_called_once()


class GuestLifecycle(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.m = load(ROOT / "bootstrap/dh-pve-media-deploy.py")

    def test_managed_vm_is_never_recreated_on_repeat_create(self):
        m = self.m
        site = "demo-home"
        storage = configuration()
        data = {"storage": storage}
        target = {"name": "media-server-demo-home",
                  "description": m.MANAGED_MEDIA_DESCRIPTION}
        with patch.object(m.Path, "exists", return_value=True), \
             patch.object(m, "config", return_value=target), \
             patch.object(m, "qm", side_effect=AssertionError("guest modified")):
            with redirect_stdout(io.StringIO()):
                m.recreate_legacy_guest_if_needed(2238, site, data, "create")

    def test_import_does_not_attempt_vm_recreation(self):
        m = self.m
        with patch.object(m, "qm", side_effect=AssertionError("guest modified")):
            m.recreate_legacy_guest_if_needed(2238, "demo-home", {}, "import")


    def test_existing_vm_preserved_by_default(self):
        m = self.m
        data = {"storage": configuration()}
        with (patch.object(m.Path, "exists", return_value=True),
              patch.object(m, "config", return_value={"name": "media-server-demo-home"}),
              patch.object(m, "qm", side_effect=AssertionError("VM changed"))):
            with redirect_stdout(io.StringIO()):
                m.recreate_legacy_guest_if_needed(2238, "demo-home", data,
                                                    "create", "keep", "preserve")

    def test_partial_first_install_vm_is_preserved_with_explicit_erase(self):
        m = self.m
        with (patch.object(m.Path, "exists", return_value=True),
              patch.object(m, "config", return_value={"name": "media-server-demo-home",
                                                       "description": m.MANAGED_MEDIA_DESCRIPTION}),
              patch.object(m, "qm", side_effect=AssertionError("VM modified"))):
            with redirect_stdout(io.StringIO()) as output:
                m.recreate_legacy_guest_if_needed(
                    2238, "demo-home", {"storage": configuration()},
                    "create", "keep", "erase")
            self.assertIn("MEDIA_VM=EXISTS_PRESERVED", output.getvalue())

    def test_import_with_erase_is_blocked(self):
        m = self.m
        with self.assertRaisesRegex(m.Blocked, "IMPORT_DISK_ERASE_CONFLICT"):
            m.recreate_legacy_guest_if_needed(2238, "demo-home", {},
                                                "import", "recreate", "erase")


    def test_managed_vm_recreated_only_after_explicit_answer(self):
        m = self.m
        data = {"storage": configuration(), "pci": {
            "gpu": "0000:00:02.0", "sata_controller": "0000:00:17.0"}}
        state = {"name": "media-server-demo-home",
                 "description": m.MANAGED_MEDIA_DESCRIPTION}
        removed = [False]
        calls = []
        def qm(cmd, *_args, **_kwargs):
            calls.append(cmd)
            if cmd == "status":
                return "status: stopped" if "shutdown" in calls else "status: running"
            if cmd == "destroy":
                removed[0] = True
            return ""
        with (patch.object(m.Path, "exists", side_effect=lambda: not removed[0]),
              patch.object(m, "config", return_value=state),
              patch.object(m, "old_guest_matches") as preflight,
              patch.object(m, "qm", side_effect=qm)):
            with redirect_stdout(io.StringIO()):
                m.recreate_legacy_guest_if_needed(
                    2238, "demo-home", data, "create", "recreate", "preserve")
        preflight.assert_called_once_with(2238, "demo-home", data, managed=True)
        self.assertIn("shutdown", calls)
        self.assertIn("destroy", calls)

    def test_legacy_vdev_must_be_on_approved_ssd(self):
        m = self.m
        data = {
            "pci": {"gpu": "0000:00:02.0", "sata_controller": "0000:00:17.0"},
            "storage": configuration(),
        }
        state = {
            "name": "media-server-demo-home",
            "scsi0": "local-lvm:vm-2238-disk-0,size=64G",
            "hostpci0": "0000:00:02.0,pcie=1",
            "hostpci1": "0000:00:17.0,pcie=1",
        }
        def guest(_vmid, *args):
            if args[0].endswith("/lsblk") and "-J" in args:
                return json.dumps({"blockdevices": [
                    {"name": "sdb", "path": "/dev/sdb", "type": "disk",
                     "serial": "ABCDEFGHI123", "wwn": "0x1234567890abcdef",
                     "size": 1000204886016}]})
            if args[0].endswith("/zpool") and "get" in args:
                return "123456789012345"
            if args[0].endswith("/zpool") and "status" in args:
                return "config:\n    /dev/sdc1 ONLINE 0 0 0"
            if args[0].endswith("/lsblk") and "-n" in args:
                return "sdc"
            raise AssertionError("unexpected guest command")
        with (patch.object(m, "config", return_value=state),
              patch.object(m, "guest_exec", side_effect=guest)):
            with self.assertRaisesRegex(m.Blocked, "LEGACY_ZFS_ON_FOREIGN_DISK"):
                m.old_guest_matches(2238, "demo-home", data)


if __name__ == "__main__":
    unittest.main()
