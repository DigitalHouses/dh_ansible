#!/usr/bin/env python3
"""Crash-recoverable root-only media password rotation transaction.

The web-facing service MUST NOT invoke this until a trusted TLS/auth boundary
has been independently accepted. This module implements only the privileged
transaction primitive, not an HTTP endpoint or credential transport.
"""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile

PASSWORD = re.compile(r"^[A-Za-z0-9]{6,8}$")
USERNAME = re.compile(r"^[a-z][a-z0-9_]{2,30}$")
JOURNAL = ".dh-media-password-rotation.json"
LOCK = ".dh-media-password-rotation.lock"


class Blocked(RuntimeError):
    pass


def require(value, reason):
    if not value:
        raise Blocked(reason)


def digest(user, password):
    return hashlib.sha256((user + "\0" + password).encode("ascii")).hexdigest()


def check_file(path, modes=(0o600,), *, owner=0):
    require(not path.is_symlink(), "ROTATION_SYMLINK_BLOCKED")
    st = path.lstat()
    require(stat.S_ISREG(st.st_mode) and st.st_uid == owner and
            stat.S_IMODE(st.st_mode) in modes and st.st_nlink == 1,
            "ROTATION_FILE_UNSAFE")


def flush_parent(directory):
    fd = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def durable_json(path, obj):
    # Only JSON metadata is logged. Secret-bearing transaction file is root-only.
    fd, tmp = tempfile.mkstemp(prefix=".dh-rotate-", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="ascii") as out:
            json.dump(obj, out, sort_keys=True, separators=(",", ":"))
            out.write("\n")
            out.flush()
            os.fsync(out.fileno())
        if path.exists() or path.is_symlink():
            check_file(path)
        os.replace(tmp, path)
        flush_parent(path.parent)
    finally:
        if os.path.lexists(tmp):
            os.unlink(tmp)


def owner_password(path):
    check_file(path, (0o600, 0o640))
    lines = path.read_text("ascii").splitlines()
    require(len(lines) == 2 and lines[0] == "username=" + path.stem
            and lines[1].startswith("password="),
            "ROTATION_PASSPORT_INVALID")
    pw = lines[1][9:]
    require(PASSWORD.fullmatch(pw) is not None, "ROTATION_PASSWORD_POLICY")
    return pw


class PassportAdapter:
    """Last commit step: atomic ZFS owner TXT replacement, never an API call."""

    def __init__(self, path):
        self.path = Path(path)

    def verify(self, password):
        return owner_password(self.path) == password

    def apply(self, previous, password):
        require(self.verify(previous), "ROTATION_PASSPORT_DRIFT")
        stat_info = self.path.stat()
        content = "username=" + self.path.stem + "\npassword=" + password + "\n"
        fd, tmp = tempfile.mkstemp(prefix=".owner-passport-", dir=self.path.parent)
        try:
            os.fchown(fd, stat_info.st_uid, stat_info.st_gid)
            os.fchmod(fd, stat.S_IMODE(stat_info.st_mode))
            with os.fdopen(fd, "w", encoding="ascii") as out:
                out.write(content)
                out.flush()
                os.fsync(out.fileno())
            os.replace(tmp, self.path)
            flush_parent(self.path.parent)
        finally:
            if os.path.lexists(tmp):
                os.unlink(tmp)


class StampAdapter:
    """Reconcile only pre-existing, root-owned service stamps."""

    def __init__(self, path, username):
        self.path = Path(path)
        self.username = username

    def verify(self, password):
        check_file(self.path)
        return self.path.read_text("ascii").strip() == digest(
            self.username, password)

    def apply(self, previous, password):
        require(self.verify(previous), "ROTATION_STAMP_DRIFT")
        durable_text(self.path, digest(self.username, password) + "\n")


def durable_text(path, value):
    check_file(path)
    fd, tmp = tempfile.mkstemp(prefix=".dh-rotate-stamp-", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="ascii") as out:
            out.write(value)
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp, path)
        flush_parent(path.parent)
    finally:
        if os.path.lexists(tmp):
            os.unlink(tmp)


def preflight_scope(path):
    require(os.geteuid() == 0, "ROTATION_REQUIRES_ROOT")
    require(path.is_absolute() and path.parent.name == "credentials"
            and path.parent.parent.name == "digitalhouses"
            and path.parent.parent.parent.name.endswith("-pool")
            and USERNAME.fullmatch(path.stem) is not None,
            "ROTATION_OWNER_SCOPE_INVALID")
    require(not path.parent.is_symlink(), "ROTATION_CREDENTIAL_DIR_SYMLINK")
    info = path.parent.lstat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0
            and stat.S_IMODE(info.st_mode) in (0o700, 0o750),
            "ROTATION_CREDENTIAL_DIR_UNSAFE")
    owner_password(path)


def transaction_file(directory):
    return directory / JOURNAL


def verify_only(adapter, password):
    try:
        return bool(adapter.verify(password))
    except (OSError, RuntimeError, ValueError):
        # Fail closed; unknown state cannot be interpreted as old/new.
        return False


def rollback(journal, adapters, save):
    journal["phase"] = "rollback"
    save(journal)
    # Include in-flight adapter. A process can die AFTER changing the remote
    # password but BEFORE recording its completion.
    touched = list(journal["applied"])
    if journal["inflight"] and journal["inflight"] not in touched:
        touched.append(journal["inflight"])
    for name in reversed(touched):
        adapter = adapters[name]
        if verify_only(adapter, journal["old"]):
            continue
        require(verify_only(adapter, journal["new"]),
                "ROTATION_ROLLBACK_UNKNOWN_" + name)
        journal["inflight"] = name
        save(journal)
        adapter.apply(journal["new"], journal["old"])
        require(verify_only(adapter, journal["old"]),
                "ROTATION_ROLLBACK_FAILED_" + name)
        journal["inflight"] = None
        journal["applied"].remove(name) if name in journal["applied"] else None
        save(journal)


def execute(path, current, requested, adapters, *, resume=False):
    """Invoke with Ordered dict adapters implementing verify and apply.

    Passwords are NOT passed in argv, exported as env vars, or printed.
    Caller must provide a complete, fixed service adapter set; missing services
    are blocked by the higher-level runtime wiring, not silently skipped.
    """
    path = Path(path)
    preflight_scope(path)
    require(isinstance(adapters, dict) and adapters
            and all(re.fullmatch(r"[a-z][a-z0-9_]*", n) for n in adapters),
            "ROTATION_ADAPTERS_INVALID")
    directory = path.parent
    lock = directory / LOCK
    if lock.exists() or lock.is_symlink():
        check_file(lock)
    fd = os.open(lock, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        os.fchmod(fd, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        record = transaction_file(directory)
        if record.exists() or record.is_symlink():
            check_file(record)
            require(resume, "ROTATION_PENDING_RECOVERY")
            journal = json.loads(record.read_text("ascii"))
            require(journal.get("schema") == 1
                    and journal.get("username") == path.stem
                    and journal.get("steps") == list(adapters)
                    and journal.get("phase") in ("apply", "rollback", "commit")
                    and isinstance(journal.get("applied"), list)
                    and all(s in adapters for s in journal["applied"])
                    and journal.get("inflight") in (None, *adapters),
                    "ROTATION_JOURNAL_INVALID")
            require(PASSWORD.fullmatch(journal.get("old", "")) is not None
                    and PASSWORD.fullmatch(journal.get("new", "")) is not None,
                    "ROTATION_JOURNAL_PASSWORD_INVALID")
        else:
            require(not resume, "ROTATION_NO_PENDING_TRANSACTION")
            require(isinstance(current, str) and isinstance(requested, str)
                    and PASSWORD.fullmatch(current) is not None
                    and PASSWORD.fullmatch(requested) is not None
                    and current != requested, "ROTATION_PASSWORD_INVALID")
            require(owner_password(path) == current,
                    "ROTATION_OLD_PASSPORT_MISMATCH")
            for name, adapter in adapters.items():
                require(verify_only(adapter, current),
                        "ROTATION_PRECHECK_FAILED_" + name)
            journal = {"schema": 1, "username": path.stem,
                       "old": current, "new": requested, "phase": "apply",
                       "steps": list(adapters), "applied": [], "inflight": None}
            durable_json(record, journal)

        def save(obj):
            durable_json(record, obj)

        def finish():
            record.unlink()
            flush_parent(directory)

        if journal["phase"] == "rollback":
            rollback(journal, adapters, save)
            require(owner_password(path) == journal["old"],
                    "ROTATION_ROLLBACK_OWNER_DRIFT")
            finish()
            return "ROLLED_BACK"

        try:
            for name, adapter in adapters.items():
                if name in journal["applied"]:
                    require(verify_only(adapter, journal["new"]),
                            "ROTATION_COMPLETED_STEP_DRIFT_" + name)
                    continue
                if verify_only(adapter, journal["new"]):
                    # Recover an interrupted adapter: last write succeeded,
                    # but the journal was not advanced yet.
                    pass
                else:
                    require(verify_only(adapter, journal["old"]),
                            "ROTATION_REMOTE_UNKNOWN_" + name)
                    journal["inflight"] = name
                    save(journal)
                    adapter.apply(journal["old"], journal["new"])
                    require(verify_only(adapter, journal["new"]),
                            "ROTATION_VERIFY_FAILED_" + name)
                journal["applied"].append(name)
                journal["inflight"] = None
                save(journal)
            journal["phase"] = "commit"
            save(journal)
            require(owner_password(path) == journal["new"],
                    "ROTATION_OWNER_NOT_COMMITTED")
            finish()
            return "COMMITTED"
        except Exception:
            # Once every adapter and the owner passport were committed,
            # a temporary verification outage must NOT silently revert the
            # customer's already-active new password.
            if journal["phase"] == "commit":
                raise Blocked("ROTATION_COMMIT_VERIFICATION_PENDING")
            rollback(journal, adapters, save)
            require(owner_password(path) == journal["old"],
                    "ROTATION_ROLLBACK_OWNER_DRIFT")
            finish()
            return "ROLLED_BACK"
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


if __name__ == "__main__":
    print("ROTATION_WEB_ENTRYPOINT=NOT_COMMISSIONED", file=sys.stderr)
    sys.exit(2)
