"""Guarded non-destructive Sambee firstboot repair contract tests."""
import importlib.util
import os
from pathlib import Path
import sqlite3
import stat
import tempfile
from unittest import mock
import yaml

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "roles/media_guest_setup/files/dh-sambee-firstboot-recovery.py"


def load():
    spec = importlib.util.spec_from_file_location("dh_sambee_firstboot_recovery", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def ready(m, root, username="admin"):
    base = root / "dh-sambee"
    base.mkdir(mode=0o755)
    (base / "config.toml").write_text('[admin]\nusername = "serzhan"\n')
    (base / "docker-compose.yml").write_text("services: {}\n")
    data = base / "data"
    data.mkdir(mode=0o750)
    conn = sqlite3.connect(str(data / "sambee.db"))
    conn.execute("create table user (username text not null)")
    conn.execute("insert into user (username) values (?)", (username,))
    for table in m.EMPTY_TABLES:
        conn.execute('create table "' + table + '" (name text)')
    conn.commit()
    conn.close()
    (data / "mobile_logs").mkdir()
    (data / "sambee.db-wal").write_bytes(b"")
    (data / "sambee.db-shm").write_bytes(b"\0" * 32)
    for path in data.iterdir():
        os.chown(path, 1000, 1000)
    os.chown(data, 1000, 1000)
    m.BASE, m.DATA, m.CONFIG, m.COMPOSE = (
        base, data, base / "config.toml", base / "docker-compose.yml")
    m.SNAPSHOTS = base / "protected-recovery"
    m.MARKER = base / ".owner-firstboot-recovery.json"
    return base, data


def test_empty_single_admin_snapshot_preserves_full_database_without_modification():
    m = load()
    with tempfile.TemporaryDirectory() as temp:
        base, data = ready(m, Path(temp))
        original = (data / "sambee.db").read_bytes()
        assert m.inspect("serzhan") == "LEGACY_EMPTY_ADMIN"
        with mock.patch.object(m, "stopped") as stop:
            m.apply("serzhan")
            stop.assert_called_once()
        assert not (data / "sambee.db").exists()
        assert data.stat().st_uid == 1000
        backup = next(m.SNAPSHOTS.iterdir())
        assert (backup / "data" / "sambee.db").read_bytes() == original
        assert (backup / "data" / "sambee.db-shm").exists()
        assert (backup / "data" / "sambee.db-wal").exists()
        assert (backup / "data" / "mobile_logs").is_dir()
        assert stat.S_IMODE(m.SNAPSHOTS.stat().st_mode) == 0o700
        assert stat.S_IMODE(backup.stat().st_mode) == 0o700
        assert stat.S_IMODE(m.MARKER.stat().st_mode) == 0o600


def test_old_database_with_smb_connection_blocks_without_any_changes():
    m = load()
    with tempfile.TemporaryDirectory() as temp:
        base, data = ready(m, Path(temp))
        conn = sqlite3.connect(str(data / "sambee.db"))
        conn.execute("insert into connection (name) values ('exists')")
        conn.commit()
        conn.close()
        try:
            m.inspect("serzhan")
        except m.Blocked as e:
            assert str(e) == "SAMBEE_RECOVERY_STATE_NOT_EMPTY_connection"
        else:
            assert False, "Active Samba connection must block Sambee DB reset"
        assert not m.SNAPSHOTS.exists()
        assert (data / "sambee.db").exists()


def test_foreign_admin_identity_never_reset():
    m = load()
    with tempfile.TemporaryDirectory() as temp:
        base, data = ready(m, Path(temp), username="other_person")
        try:
            m.inspect("serzhan")
        except m.Blocked as e:
            assert str(e) == "SAMBEE_RECOVERY_FOREIGN_IDENTITY"
        else:
            assert False, "Unknown identity must block"


def test_unknown_persistent_files_never_reset():
    m = load()
    with tempfile.TemporaryDirectory() as temp:
        base, data = ready(m, Path(temp))
        (data / "unapproved-file").write_text("user-owned")
        try:
            m.inspect("serzhan")
        except m.Blocked as e:
            assert str(e) == "SAMBEE_RECOVERY_UNKNOWN_PERSISTENT_DATA"
        else:
            assert False
        assert (data / "sambee.db").exists()


def test_repeated_recovery_refuses_to_overwrite_snapshot_or_live_data():
    m = load()
    with tempfile.TemporaryDirectory() as temp:
        base, data = ready(m, Path(temp))
        with mock.patch.object(m, "stopped"):
            m.apply("serzhan")
        # Simulate Sambee starting once with configured owner.
        conn = sqlite3.connect(str(data / "sambee.db"))
        conn.execute("create table user (username text not null)")
        conn.execute("insert into user (username) values ('serzhan')")
        conn.commit()
        conn.close()
        os.chown(data / "sambee.db", 1000, 1000)
        assert m.inspect("serzhan") == "OWNER_EXISTS"
        original_backup = next(m.SNAPSHOTS.iterdir())
        with mock.patch.object(m, "stopped") as stop:
            try:
                m.apply("serzhan")
            except m.Blocked as e:
                assert str(e) == "SAMBEE_RECOVERY_NOT_REQUIRED"
            else:
                assert False
            stop.assert_not_called()
        assert next(m.SNAPSHOTS.iterdir()) == original_backup


def test_analyzed_firstboot_is_only_triggered_for_safe_state_before_native_login():
    apps = yaml.safe_load(
        (ROOT / "roles/media_guest_setup/tasks/apps.yml").read_text())
    names = [a["name"] for a in apps]
    expected = [
        "Inspect whether empty preexisting Sambee admin state needs recovery",
        "Stop only empty preexisting Sambee bootstrap container for protected recovery",
        "Preserve entire legacy Sambee database with atomic root-only snapshot",
        "Recreate only the Sambee container against verified clean application data",
        "Wait for Sambee HTTP UI",
        "Verify fresh Sambee bootstrap created the Fleet owner before password reconciliation",
        "Commission Sambee using the shared eight-character owner password",
    ]
    assert all(name in names for name in expected)
    assert [names.index(name) for name in expected] == sorted(
        names.index(name) for name in expected)
    for name in expected[1:4]:
        item = apps[names.index(name)]
        assert "'SAMBEE_RECOVERY=REQUIRED' in media_sambee_firstboot_recovery.stdout_lines" in item["when"]
    assert "backup" not in str(ROOT / "roles/media_guest_setup/templates/media-sambee-compose.yml.j2")
    boot = (ROOT / "bootstrap/semaphore-host-install.py").read_text()
    assert '"roles/media_guest_setup/files/dh-sambee-firstboot-recovery.py"' in boot
    assert '("media_guest_setup", "files/dh-sambee-firstboot-recovery.py")' in boot


def test_helper_has_no_hardcoded_site_storage_and_no_db_mutation():
    source = SCRIPT.read_text()
    assert "serzhan-home-pool" not in source
    assert "DELETE FROM" not in source and "UPDATE user" not in source
    assert 'os.replace(DATA, target / "data")' in source
