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

    def test_malformed_layout_rejected_without_device_lookups(self):
        data = configuration()
        data["layout"]["directories"] = ["bad/.."]
        with patch.object(self.m, "find_disk", side_effect=AssertionError("disk touched")):
            with self.assertRaises(self.m.Blocked):
                self.m.validate_layout(data)

    def test_import_only_verifies_existing_pool_on_selected_disk(self):
        m = self.m
        with patch.object(m, "get_pool", return_value="445566778899"), \
             patch.object(m, "verify_pool_disk") as verify, \
             patch.object(m, "command", side_effect=AssertionError("disk write attempted")):
            assert m.ensure_import("demo-home-pool", "/dev/sdb",
                                   "/dev/sdb", "readwrite") == ("445566778899", False)
        verify.assert_called_once_with("demo-home-pool", "/dev/sdb")

    def test_explicit_erase_does_not_require_legacy_site_flag(self):
        m = self.m
        data = {"pool_name": "demo-home-pool"}
        with patch.object(m, "active_pools", return_value=[]), \
             patch.object(m, "scan_devices", return_value=["/dev/sdb"]), \
             patch.object(m, "get_pool", return_value="123"), \
             patch.object(m, "verify_pool_disk"), \
             patch.object(m, "command", return_value=""):
            assert m.erase_disk(data, "/dev/sdb") == ("123", True)

    def test_explicit_erase_recreates_canonical_pool_regardless_of_previous_layout(self):
        m = self.m
        calls = []
        def cmd(*args, **kw):
            calls.append(args)
            return ""
        with patch.object(m, "active_pools", return_value=[]), \
             patch.object(m, "scan_devices", return_value=["/dev/sdb", "/dev/sdb1"]), \
             patch.object(m, "get_pool", return_value="445566778899"), \
             patch.object(m, "verify_pool_disk"), \
             patch.object(m, "command", side_effect=cmd):
            with redirect_stdout(io.StringIO()):
                assert m.erase_disk(configuration(), "/dev/sdb") == ("445566778899", True)
        assert ("/usr/sbin/sgdisk", "--zap-all", "/dev/sdb") in calls
        assert ("/usr/sbin/wipefs", "--all", "--force", "/dev/sdb1") in calls
        assert any(a[:2] == ("/usr/sbin/zpool", "create") for a in calls)


class GuestLifecycle(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.m = load(ROOT / "bootstrap/dh-pve-media-deploy.py")

    def test_create_rejects_existing_vm_without_qm_mutation(self):
        m = self.m
        with (patch.object(m.Path, "exists", return_value=True),
              patch.object(m, "config", return_value={"name": "media-server-demo-home"}),
              patch.object(m, "qm", side_effect=AssertionError("VM modified"))):
            with self.assertRaisesRegex(m.Blocked, "VM_CREATE_REQUIRES_ABSENT_VM"):
                m.reconcile_media_vm_lifecycle(2238, "demo-home", {},
                                                "create", "import")

    def test_create_requires_absent_vm_and_does_not_call_qm(self):
        m = self.m
        with (patch.object(m.Path, "exists", return_value=False),
              patch.object(m, "qm", side_effect=AssertionError("VM modified"))):
            with redirect_stdout(io.StringIO()) as output:
                m.reconcile_media_vm_lifecycle(2238, "demo-home", {},
                                                "create", "erase")
            self.assertIn("MEDIA_VM=ABSENT_CREATE_FRESH", output.getvalue())

    def test_recreate_requires_existing_vm(self):
        m = self.m
        with (patch.object(m.Path, "exists", return_value=False),
              patch.object(m, "qm", side_effect=AssertionError("VM modified"))):
            with self.assertRaisesRegex(m.Blocked,
                                        "VM_RECREATE_REQUIRES_EXISTING_VM"):
                m.reconcile_media_vm_lifecycle(2238, "demo-home", {},
                                                "recreate", "erase")

    def test_keep_requires_existing_vm(self):
        m = self.m
        with (patch.object(m.Path, "exists", return_value=False),
              patch.object(m, "qm", side_effect=AssertionError("VM modified"))):
            with self.assertRaisesRegex(m.Blocked,
                                        "VM_KEEP_REQUIRES_EXISTING_VM"):
                m.reconcile_media_vm_lifecycle(2238, "demo-home", {},
                                                "keep", "import")

    def test_existing_vm_preserved_for_both_raw_disk_choices(self):
        m = self.m
        for raw_action in ("erase", "import"):
            with (patch.object(m.Path, "exists", return_value=True),
                  patch.object(m, "config", return_value={"name": "mediaserver-demo-home"}),
                  patch.object(m, "qm", side_effect=AssertionError("VM changed"))):
                with redirect_stdout(io.StringIO()) as output:
                    m.reconcile_media_vm_lifecycle(
                        2238, "demo-home", {"name": "mediaserver-demo-home"}, "keep", raw_action)
                self.assertIn("MEDIA_VM=EXISTS_PRESERVED", output.getvalue())

    def test_recreate_validated_for_both_raw_disk_choices(self):
        m = self.m
        for raw_action in ("import", "erase"):
            data = {"name": "mediaserver-demo-home", "storage": configuration()}
            state = {"name": "media-server-demo-home",
                     "description": m.MANAGED_MEDIA_DESCRIPTION}
            removed = [False]
            calls = []

            def qm(command, *_args, **_kwargs):
                calls.append(command)
                if command == "status":
                    return "status: stopped" if "shutdown" in calls else "status: running"
                if command == "destroy":
                    removed[0] = True
                return ""
            with (patch.object(m.Path, "exists", side_effect=lambda: not removed[0]),
                  patch.object(m, "config", return_value=state),
                  patch.object(m, "old_guest_matches") as preflight,
                  patch.object(m, "run", return_value="[]"),
                  patch.object(m, "qm", side_effect=qm)):
                with redirect_stdout(io.StringIO()):
                    m.reconcile_media_vm_lifecycle(
                        2238, "demo-home", data, "recreate", raw_action)
            preflight.assert_called_once_with(2238, "demo-home", data, managed=True)
            self.assertIn("shutdown", calls)
            self.assertIn("destroy", calls)

    def test_unmanaged_vm_recreate_is_always_blocked(self):
        m = self.m
        data = {"name": "mediaserver-demo-home", "storage": configuration()}
        state = {"name": "media-server-demo-home", "description": "legacy"}
        with (patch.object(m.Path, "exists", return_value=True),
              patch.object(m, "config", return_value=state),
              patch.object(m, "old_guest_matches",
                           side_effect=m.Blocked("FOREIGN_VM_BLOCKED")),
              patch.object(m, "qm", side_effect=AssertionError("VM mutated"))):
            with self.assertRaisesRegex(m.Blocked, "FOREIGN_VM_BLOCKED"):
                m.reconcile_media_vm_lifecycle(2238, "demo-home", data,
                                                "recreate", "erase")

    def test_invalid_vm_or_disk_selects_fail_closed(self):
        m = self.m
        for vm_choice, disk_choice in (
                ("unknown", "erase"), ("keep", "preserve"), ("import", "create")):
            with (patch.object(m.Path, "exists", side_effect=AssertionError(
                    "VM existence accessed for invalid choices")),
                  self.assertRaises(m.Blocked)):
                m.reconcile_media_vm_lifecycle(2238, "demo-home", {},
                                                vm_choice, disk_choice)

    def test_managed_vm_requires_exact_sata_pci_controller(self):
        m = self.m
        data = {
            "name": "mediaserver-demo-home",
            "pci": {"gpu": "0000:00:02.0", "sata_controller": "0000:00:17.0"},
            "storage": configuration(),
        }
        state = {
            "name": "mediaserver-demo-home",
            "description": m.MANAGED_MEDIA_DESCRIPTION,
            "scsi0": "local-lvm:vm-2238-disk-0,size=64G",
            "hostpci0": "0000:00:02.0,pcie=1",
            "hostpci1": "0000:00:19.0,pcie=1",
        }
        with (patch.object(m, "config", return_value=state),
              patch.object(m, "guest_exec",
                           side_effect=AssertionError("guest should not be probed"))):
            with self.assertRaisesRegex(m.Blocked, "SATA_CONFIG_DRIFT"):
                m.old_guest_matches(2238, "demo-home", data, managed=True)

if __name__ == "__main__":
    unittest.main()
