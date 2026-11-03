"""Single SATA controller discovery: no user disk selection or WWN."""
import importlib.util
import json
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "roles/media_guest_setup/files/dh-media-storage.py"
SPEC = importlib.util.spec_from_file_location("media_sata_storage_modes", SRC)
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)

CONFIG = {"sata_controller": {"vendor": "0x1234", "device": "0xabcd"}}


def discover(disks, *, parents=None, root="vda1\nvda"):
    parents = parents or {name: ("0x1234", "0xabcd") for name in disks}
    devices = {"blockdevices": [
        {"name": name, "path": "/dev/" + name, "type": "disk", "tran": "sata"}
        for name in disks]}
    def command(*args, **kwargs):
        if args[0] == "/usr/bin/lsblk" and "-J" in args:
            return json.dumps(devices)
        if args[0] == "/usr/bin/findmnt":
            return "/dev/vda1"
        if args[0] == "/usr/bin/lsblk" and "-s" in args:
            return root
        raise AssertionError("Unexpected command: " + str(args))
    with patch.object(m.Path, "exists", return_value=False), \
         patch.object(m.Path, "is_block_device", return_value=True), \
         patch.object(m, "commissioned_controller", return_value="/guest/pci/approved"), \
         patch.object(m, "sata_pci_parent", side_effect=lambda name: (
             "/guest/pci/approved" if parents[name] == ("0x1234", "0xabcd")
             else "/guest/pci/other")), \
         patch.object(m, "command", side_effect=command):
        return m.find_disk(CONFIG)


def test_no_disks_on_passed_sata_controller_blocks(capsys):
    with pytest.raises(m.Blocked, match="SATA_NO_DISKS"):
        discover([])
    assert "MEDIA_SATA_DISKS_FOUND=0" in capsys.readouterr().out


def test_exactly_one_disk_is_selected_by_pci_ancestry_without_serial(capsys):
    assert discover(["sdb"]) == ("/dev/sdb", "/dev/sdb")
    assert "MEDIA_SATA_DISKS_FOUND=1" in capsys.readouterr().out


def test_multiple_disks_on_commissioned_sata_controller_block(capsys):
    with pytest.raises(m.Blocked, match="SATA_MULTIPLE_DISKS"):
        discover(["sdb", "sdc"])
    assert "MEDIA_SATA_DISKS_FOUND=2" in capsys.readouterr().out


def test_other_controller_disks_are_not_candidates():
    assert discover(["sdb", "sdc"], parents={
        "sdb": ("0x1234", "0xabcd"),
        "sdc": ("0x8086", "0x2922"),
    }) == ("/dev/sdb", "/dev/sdb")


def test_system_disk_is_never_erasable_even_if_attached_to_sata():
    with pytest.raises(m.Blocked, match="SATA_DISK_IS_SYSTEM_DISK"):
        discover(["sdb"], root="sdb1\nsdb")


def test_no_vendor_device_ids_blocks_even_with_one_disk():
    with patch.object(m.Path, "exists", return_value=False), \
         pytest.raises(m.Blocked, match="SATA_CONTROLLER_PCI_ID_NOT_COMMISSIONED"):
        m.find_disk({"sata_controller": {"vendor": "", "device": ""}})


def test_pve_recreate_keeps_vm_identity_boundaries_not_old_pool_guids():
    source = (ROOT / "bootstrap/dh-pve-media-deploy.py").read_text()
    gate = source.split("def old_guest_matches(", 1)[1].split(
        "def reconcile_media_vm_lifecycle(", 1)[0]
    assert "FOREIGN_VM_BLOCKED" in gate
    assert "SATA_CONFIG_DRIFT" in gate
    assert "UNEXPECTED_VM_DISK_" in gate
    assert "import_pool_guid" not in gate


def test_import_handler_and_boot_path_have_no_disk_erase():
    source = SRC.read_text()
    body = source.split("def ensure_import(", 1)[1].split(
        "def active_pools(", 1)[0]
    assert all(x not in body for x in ("wipefs", "sgdisk", "destroy", "zpool create"))
    boot = (ROOT / "roles/media_guest_setup/templates/dh-media-zfs-mount.sh.j2").read_text()
    assert "--action import" in boot
    assert "--action erase" not in boot


@pytest.mark.parametrize("matching_count", [0, 1, 2])
def test_only_one_matching_sata_pci_controller_is_allowed(tmp_path, matching_count):
    root = tmp_path / "pci" / "devices"
    root.mkdir(parents=True)
    for idx in range(matching_count):
        node = root / f"0000:00:{idx+1:02x}.0"
        node.mkdir()
        (node / "class").write_text("0x010601\\n")
        (node / "vendor").write_text("0x1234\\n")
        (node / "device").write_text("0xabcd\\n")
    original_path = Path
    with patch.object(m, "Path", side_effect=lambda value: (
            root if str(value) == "/sys/bus/pci/devices"
            else original_path(value))):
        if matching_count != 1:
            with pytest.raises(m.Blocked, match="SATA_CONTROLLER_NOT_UNIQUE_IN_GUEST"):
                m.commissioned_controller(CONFIG["sata_controller"])
        else:
            assert m.commissioned_controller(CONFIG["sata_controller"]) == str(
                (root / "0000:00:01.0").resolve())
