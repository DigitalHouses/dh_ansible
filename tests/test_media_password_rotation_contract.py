"""Password rotation transaction tests; no network, PVE, or production changes."""
import importlib.util
import os
from pathlib import Path
import tempfile
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "roles/media_guest_setup/files/dh-media-password-transaction.py"


def transaction():
    spec = importlib.util.spec_from_file_location("media_rotation_core", MODULE)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


class LocalApp:
    def __init__(self, password, fail=False, crash=False):
        self.password = password
        self.fail = fail
        self.crash = crash
        self.calls = 0

    def verify(self, password):
        return self.password == password

    def apply(self, previous, requested):
        assert previous == self.password
        self.password = requested
        self.calls += 1
        if self.fail:
            self.fail = False
            raise RuntimeError("simulated network failure after remote mutation")
        if self.crash:
            self.crash = False
            raise SystemExit(17)


def rig(directory, rotation):
    root = directory / "example-pool" / "digitalhouses" / "credentials"
    root.mkdir(parents=True)
    root.chmod(0o750)
    owner = root / "owner.txt"
    owner.write_text("username=owner\npassword=Original\n", encoding="ascii")
    owner.chmod(0o640)
    stamps = []
    for name in ("qbt", "radarr"):
        stamp = root / ("." + name + ".sha256")
        stamp.write_text(rotation.digest("owner", "Original") + "\n")
        stamp.chmod(0o600)
        stamps.append((name, stamp))
    return root, owner, stamps


def adapters(rotation, owner, stamps, a, b):
    return {
        "jellyfin": a,
        "sambee": b,
        "qbt_stamp": rotation.StampAdapter(stamps[0][1], "owner"),
        "arr_stamp": rotation.StampAdapter(stamps[1][1], "owner"),
        "passport": rotation.PassportAdapter(owner),
    }


def test_rotation_commits_app_passwords_stamps_and_passport_only_after_verification():
    rot = transaction()
    with tempfile.TemporaryDirectory() as d:
        root, owner, stamps = rig(Path(d), rot)
        a, b = LocalApp("Original"), LocalApp("Original")
        result = rot.execute(owner, "Original", "Newpass8",
                             adapters(rot, owner, stamps, a, b))
        assert result == "COMMITTED"
        assert rot.owner_password(owner) == "Newpass8"
        assert a.password == b.password == "Newpass8"
        assert not (root / rot.JOURNAL).exists()
        assert owner.stat().st_mode & 0o777 == 0o640
        for _, stamp in stamps:
            assert stamp.read_text().strip() == rot.digest("owner", "Newpass8")
            assert stamp.stat().st_mode & 0o777 == 0o600


def test_rotation_rolls_back_partial_remote_write_even_before_marking_step_complete():
    rot = transaction()
    with tempfile.TemporaryDirectory() as d:
        root, owner, stamps = rig(Path(d), rot)
        a, b = LocalApp("Original"), LocalApp("Original", fail=True)
        outcome = rot.execute(owner, "Original", "Newpass8",
                              adapters(rot, owner, stamps, a, b))
        assert outcome == "ROLLED_BACK"
        assert a.password == b.password == "Original"
        assert rot.owner_password(owner) == "Original"
        assert all(p.read_text().strip() == rot.digest("owner", "Original")
                   for _, p in stamps)
        assert not (root / rot.JOURNAL).exists()


def test_interrupted_remote_write_recovers_idempotently_from_root_only_journal():
    rot = transaction()
    with tempfile.TemporaryDirectory() as d:
        root, owner, stamps = rig(Path(d), rot)
        a, b = LocalApp("Original"), LocalApp("Original", crash=True)
        steps = adapters(rot, owner, stamps, a, b)
        try:
            rot.execute(owner, "Original", "Newpass8", steps)
        except SystemExit:
            pass
        else:
            assert False, "Test must simulate a crashed process"
        pending = root / rot.JOURNAL
        assert pending.stat().st_mode & 0o777 == 0o600
        assert a.password == b.password == "Newpass8"
        try:
            rot.execute(owner, "Original", "Otherpw8", steps)
        except rot.Blocked as exc:
            assert str(exc) == "ROTATION_PENDING_RECOVERY"
        else:
            assert False, "Pending journal must block a second rotation"
        assert rot.execute(owner, None, None, steps, resume=True) == "COMMITTED"
        assert rot.owner_password(owner) == "Newpass8"
        assert b.calls == 1
        assert not pending.exists()


def test_incorrect_old_password_or_invalid_length_does_not_write_journal():
    rot = transaction()
    with tempfile.TemporaryDirectory() as d:
        root, owner, stamps = rig(Path(d), rot)
        a, b = LocalApp("Original"), LocalApp("Original")
        for old, new in (("Incorrect", "Newpass8"),
                         ("Original", "abc"),
                         ("Original", "morethan8")):
            try:
                rot.execute(owner, old, new,
                            adapters(rot, owner, stamps, a, b))
            except rot.Blocked:
                pass
            else:
                assert False, "Bad inputs must be rejected"
        assert not (root / rot.JOURNAL).exists()
        assert a.calls == b.calls == 0


def test_broken_rollback_keeps_recovery_journal_and_blocks_second_request():
    rot = transaction()
    class Offline(LocalApp):
        def apply(self, previous, requested):
            if previous == "Newpass8":
                raise RuntimeError("cannot restore previous password")
            return super().apply(previous, requested)

    with tempfile.TemporaryDirectory() as d:
        root, owner, stamps = rig(Path(d), rot)
        a, b = Offline("Original"), LocalApp("Original", fail=True)
        steps = adapters(rot, owner, stamps, a, b)
        try:
            rot.execute(owner, "Original", "Newpass8", steps)
        except RuntimeError:
            pass
        else:
            assert False, "Rollback must fail closed"
        assert (root / rot.JOURNAL).exists()
        assert owner.read_text().endswith("password=Original\n")
        assert a.password == "Newpass8"
        # Service becomes healthy again; explicitly recover the journal.
        a.apply = LocalApp.apply.__get__(a, Offline)
        assert rot.execute(owner, None, None, steps, resume=True) == "ROLLED_BACK"
        assert a.password == b.password == "Original"
        assert not (root / rot.JOURNAL).exists()
