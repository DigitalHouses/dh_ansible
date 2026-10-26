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
                m.ensure_create(data, "/dev/disk/by-id/wwn-0x1234567890abcdef", "/dev/sdb")

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


if __name__ == "__main__":
    unittest.main()
