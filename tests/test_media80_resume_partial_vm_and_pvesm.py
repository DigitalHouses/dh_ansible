"""Regression tests for Task 80 Proxmox storage and partial VM recovery."""
import importlib.util
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]


def module(path):
    spec = importlib.util.spec_from_file_location(path.stem.replace("-", "_"), path)
    obj = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obj)
    return obj


def test_local_snippets_enablement_reads_pmxcfs_not_nonexistent_pvesm_config(tmp_path):
    m = module(ROOT / "bootstrap/dh-pve-media-deploy.py")
    config = tmp_path / "storage.cfg"
    config.write_text(
        "dir: local\n    path /var/lib/vz\n"
        "    content iso,backup,vztmpl\n"
        "\nlvmthin: local-lvm\n    thinpool data\n"
        "    vgname pve\n    content images,rootdir\n")
    real_path = Path
    called = []

    def fake_run(*argv):
        called.append(argv)
        assert argv[:4] == ("/usr/sbin/pvesm", "set", "local", "--content")
        assert set(argv[4].split(",")) == {
            "iso", "backup", "vztmpl", "snippets"}
        config.write_text(config.read_text().replace(
            "content iso,backup,vztmpl", "content " + argv[4]))
        return ""

    with (patch.object(m, "Path", side_effect=lambda path: (
            config if str(path) == "/etc/pve/storage.cfg" else real_path(path))),
          patch.object(m, "run", side_effect=fake_run)):
        m.ensure_local_snippets_storage()
        m.ensure_local_snippets_storage()
    assert len(called) == 1
    assert "content images,rootdir" in config.read_text()


@pytest.mark.parametrize("text", [
    "dir: local\n    path /somewhere\n    content iso,backup\n",
    "nfs: local\n    path /var/lib/vz\n    content iso,backup\n",
    "dir: local\n    path /var/lib/vz\n    content iso,backup\n"
    "dir: local\n    path /var/lib/vz\n    content iso,backup\n",
])
def test_foreign_or_ambiguous_storage_never_modified(tmp_path, text):
    m = module(ROOT / "bootstrap/dh-pve-media-deploy.py")
    config = tmp_path / "storage.cfg"
    config.write_text(text)
    real_path = Path
    with (patch.object(m, "Path", side_effect=lambda path: (
            config if str(path) == "/etc/pve/storage.cfg" else real_path(path))),
          patch.object(m, "run",
                       side_effect=AssertionError("must not touch pvesm"))):
        with pytest.raises(m.Blocked, match="QGA_CLOUDINIT_LOCAL_"):
            m.ensure_local_snippets_storage()


def test_failed_pvesm_set_must_not_recurse_forever(tmp_path):
    m = module(ROOT / "bootstrap/dh-pve-media-deploy.py")
    config = tmp_path / "storage.cfg"
    config.write_text("dir: local\n    path /var/lib/vz\n"
                      "    content iso,backup,vztmpl\n")
    real_path = Path
    with (patch.object(m, "Path", side_effect=lambda path: (
            config if str(path) == "/etc/pve/storage.cfg" else real_path(path))),
          patch.object(m, "run", return_value="") as invoke):
        with pytest.raises(m.Blocked, match="SNIPPETS_NOT_PERSISTED"):
            m.ensure_local_snippets_storage()
    invoke.assert_called_once()


def test_missing_pvesm_config_subcommand_is_never_called():
    s = (ROOT / "bootstrap/dh-pve-media-deploy.py").read_text()
    block = s.split("def ensure_local_snippets_storage(", 1)[1].split(
        "def ensure_qga_cloud_init(", 1)[0]
    assert '"/etc/pve/storage.cfg"' in block
    assert '"config", "local"' not in block


def data():
    return {"name": "mediaserver-serzhan",
            "pci": {"gpu": "0000:00:02.0",
                    "sata_controller": "0000:2b:00.0"}}


def stage():
    return {
        "name": "mediaserver-serzhan",
        "description": "DigitalHouses managed media guest v2",
        "ostype": "l26", "machine": "q35",
        "scsihw": "virtio-scsi-single",
        "net0": "virtio=AA:BB:CC:DD:EE:FF,bridge=vmbr0",
        "scsi0": "local-lvm:vm-2232-disk-0,discard=on",
    }


def test_managed_stopped_partial_vm_is_resumable_without_vm_destruction():
    m = module(ROOT / "bootstrap/dh-pve-media-deploy.py")
    commands = []
    def qm(*args):
        commands.append(args)
        if args[0] == "status":
            return "status: stopped"
        raise AssertionError("no destructive qm commands allowed")
    with (patch.object(m, "config", return_value=stage()),
          patch.object(m, "qm", side_effect=qm),
          patch.object(m, "qga_cicustom", return_value=None)):
        m.staged_guest_matches(2232, data())
    assert not any(x[0] in ("destroy", "create", "stop") for x in commands)


@pytest.mark.parametrize("change,reason", [
    ({"description": "not managed"}, "PARTIAL_MEDIA_VM_IDENTITY_MISMATCH"),
    ({"scsi0": "local-lvm:vm-2253-disk-0"},
     "PARTIAL_MEDIA_VM_FOREIGN_DISK"),
    ({"hostpci1": "0000:00:17.0,pcie=1"},
     "PARTIAL_MEDIA_VM_PCI_MISMATCH"),
    ({"scsi1": "local-lvm:vm-2232-disk-1"},
     "PARTIAL_MEDIA_VM_UNEXPECTED_DEVICE"),
])
def test_foreign_staged_vm_blocks_before_any_modification(change, reason):
    m = module(ROOT / "bootstrap/dh-pve-media-deploy.py")
    with (patch.object(m, "config", return_value=stage() | change),
          patch.object(m, "qm", return_value="status: stopped"),
          patch.object(m, "qga_cicustom", return_value=None)):
        with pytest.raises(m.Blocked, match=reason):
            m.staged_guest_matches(2232, data())


def test_running_incomplete_vm_is_not_treated_as_safe_resume():
    m = module(ROOT / "bootstrap/dh-pve-media-deploy.py")
    with (patch.object(m, "config", return_value=stage()),
          patch.object(m, "qm", return_value="status: running")):
        with pytest.raises(m.Blocked, match="PARTIAL_MEDIA_VM_MUST_BE_STOPPED"):
            m.staged_guest_matches(2232, data())
