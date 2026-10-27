"""Offline unit coverage of the privileged PVE NFS backup storage adapter."""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import importlib.util

SOURCE = Path(__file__).resolve().parents[1] / "bootstrap/dh-pve-media-deploy.py"


def module():
    spec = importlib.util.spec_from_file_location("media_pve_nfs", SOURCE)
    obj = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obj)
    return obj


def desired():
    data = {
        "name": "mediaserver-serzhan",
        "vmid": 2238,
        "network": {"ipv4": "192.168.11.238/24"},
        "storage": {"pool_name": "serzhan-home-pool"},
        "backup_export": {"storage_id": "dh_media_backups",
                          "path": "/serzhan-home-pool/backups/pve",
                          "pve_host": "192.168.11.30"},
    }
    return data, {"host": "192.168.11.30"}


def existing_config():
    return ("dir: local\n\tpath /var/lib/vz\n\tcontent backup\n"
            "nfs: dh_media_backups\n"
            "\tpath /mnt/pve/dh_media_backups\n"
            "\tserver 192.168.11.238\n"
            "\texport /serzhan-home-pool/backups/pve\n"
            "\tcontent backup\n")


def test_existing_exact_nfs_map_is_idempotent():
    m = module()
    d, pve = desired()
    with TemporaryDirectory() as tmp:
        cfg = Path(tmp) / "storage.cfg"
        cfg.write_text(existing_config())
        with patch.object(m, "Path", lambda _: cfg), \
             patch.object(m, "qm", lambda *a: "status: running"), \
             patch.object(m, "config", lambda _: {"name": d["name"]}), \
             patch.object(m, "run", side_effect=AssertionError("MUST_NOT_MUTATE")):
            m.ensure_backup_storage("serzhan-home", d, pve)


def test_new_backup_map_checks_nfs_export_before_registration():
    m = module()
    d, pve = desired()
    calls = []

    def fake_run(*args, **kwargs):
        calls.append(args)
        if args[1:3] == ("scan", "nfs"):
            return "/serzhan-home-pool/backups/pve accepted"
        if args[1:3] == ("add", "nfs"):
            return ""
        raise AssertionError("UNEXPECTED_ACTION")

    with TemporaryDirectory() as tmp:
        cfg = Path(tmp) / "storage.cfg"
        cfg.write_text("dir: local\n\tpath /var/lib/vz\n")
        with patch.object(m, "Path", lambda _: cfg), \
             patch.object(m, "qm", lambda *a: "status: running"), \
             patch.object(m, "config", lambda _: {"name": d["name"]}), \
             patch.object(m, "run", side_effect=fake_run):
            m.ensure_backup_storage("serzhan-home", d, pve)
    assert len(calls) == 2
    assert calls[0][1:3] == ("scan", "nfs")
    assert calls[1][1:4] == ("add", "nfs", "dh_media_backups")
    assert "--content" in calls[1] and "backup" in calls[1]


def test_mismatched_existing_storage_refuses_changes():
    m = module()
    d, pve = desired()
    with TemporaryDirectory() as tmp:
        cfg = Path(tmp) / "storage.cfg"
        cfg.write_text(existing_config().replace("192.168.11.238", "198.51.100.2"))
        with patch.object(m, "Path", lambda _: cfg), \
             patch.object(m, "qm", lambda *a: "status: running"), \
             patch.object(m, "config", lambda _: {"name": d["name"]}), \
             patch.object(m, "run", side_effect=AssertionError("MUST_NOT_MUTATE")):
            try:
                m.ensure_backup_storage("serzhan-home", d, pve)
            except m.Blocked as exc:
                assert "BACKUP_STORAGE_CONFIG_DRIFT" in str(exc)
            else:
                raise AssertionError("DRIFT_WAS_ACCEPTED")
