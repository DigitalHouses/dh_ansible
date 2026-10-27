#!/usr/bin/env python3
"""Guarded one-time Sambee bootstrap identity recovery.

Only repairs an empty, previously initialized default 'admin' database
when the declared owner is different. Existing application state is moved
unchanged into a root-only snapshot; ZFS, media and other apps untouched.
Never prints passwords, API tokens or contents of the user table.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import subprocess
import sys
import tomllib

BASE = Path("/opt/dh-sambee")
DATA = BASE / "data"
CONFIG = BASE / "config.toml"
COMPOSE = BASE / "docker-compose.yml"
SNAPSHOTS = BASE / "protected-recovery"
MARKER = BASE / ".owner-firstboot-recovery.json"
OWNER_RE = re.compile(r"^[a-z][a-z0-9_]{2,30}$")
ALLOWED = {"sambee.db", "sambee.db-wal", "sambee.db-shm", "mobile_logs"}
EMPTY_TABLES = (
    "connection", "transfer_operations", "recentdirectory", "recentfile",
    "edit_locks", "oidcidentity", "oidcproviderconfiguration"
)


class Blocked(RuntimeError):
    pass


def require(value, reason):
    if not value:
        raise Blocked(reason)


def directory(path, uid=None):
    require(not path.is_symlink(), "SAMBEE_RECOVERY_SYMLINK")
    st = path.lstat()
    require(stat.S_ISDIR(st.st_mode) and (uid is None or st.st_uid == uid),
            "SAMBEE_RECOVERY_DIRECTORY_UNSAFE")
    return st


def regular(path, uid=None):
    require(not path.is_symlink(), "SAMBEE_RECOVERY_SYMLINK")
    st = path.lstat()
    require(stat.S_ISREG(st.st_mode) and st.st_nlink == 1
            and (uid is None or st.st_uid == uid),
            "SAMBEE_RECOVERY_FILE_UNSAFE")
    return st


def owner(passport):
    directory(BASE, 0)
    regular(CONFIG, 0)
    regular(COMPOSE, 0)
    cfg = tomllib.loads(CONFIG.read_text("utf-8"))
    username = cfg.get("admin", {}).get("username")
    require(isinstance(username, str) and OWNER_RE.fullmatch(username) is not None,
            "SAMBEE_RECOVERY_OWNER_CONFIG_INVALID")
    require(passport.is_absolute() and passport.parent.name == "credentials"
            and passport.parent.parent.name == "digitalhouses"
            and passport.parent.parent.parent.parent == Path("/")
            and re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_-]{2,62}",
                             passport.parent.parent.parent.name)
            and passport.stem == username,
            "SAMBEE_RECOVERY_PASSPORT_SCOPE")
    info = regular(passport, 0)
    require(stat.S_IMODE(info.st_mode) == 0o640,
            "SAMBEE_RECOVERY_OWNER_PASSPORT_UNSAFE")
    lines = passport.read_text("ascii").splitlines()
    require(len(lines) == 2 and lines[0] == "username=" + username
            and lines[1].startswith("password=") and 6 <= len(lines[1][9:]) <= 8
            and lines[1][9:].isascii() and lines[1][9:].isalnum(),
            "SAMBEE_RECOVERY_PASSPORT_MISMATCH")
    return username


def count(db, table):
    matches = db.execute(
        "select count(*) from sqlite_master where type='table' and name=?",
        (table,)).fetchone()[0]
    require(matches == 1, "SAMBEE_RECOVERY_EXPECTED_TABLE_MISSING")
    return db.execute("select count(*) from \"" + table + "\"").fetchone()[0]


def inspect(username):
    if MARKER.exists() or MARKER.is_symlink():
        regular(MARKER, 0)
        mark = json.loads(MARKER.read_text("ascii"))
        require(mark.get("schema") == 1 and mark.get("owner") == username
                and isinstance(mark.get("backup"), str)
                and mark["backup"].startswith(str(SNAPSHOTS) + "/"),
                "SAMBEE_RECOVERY_MARKER_INVALID")
        require((Path(mark["backup"]) / "data" / "sambee.db").is_file(),
                "SAMBEE_RECOVERY_SNAPSHOT_LOST")
        already = True
    else:
        already = False

    directory(DATA, 1000)
    elements = {x.name for x in DATA.iterdir()}
    require(elements <= ALLOWED, "SAMBEE_RECOVERY_UNKNOWN_PERSISTENT_DATA")
    if "mobile_logs" in elements:
        directory(DATA / "mobile_logs", 1000)
        require(not list((DATA / "mobile_logs").iterdir()),
                "SAMBEE_RECOVERY_USER_LOGS_EXIST")
    for filename in elements - {"mobile_logs"}:
        regular(DATA / filename, 1000)
    if "sambee.db" not in elements:
        require(not already, "SAMBEE_RECOVERY_INCOMPLETE")
        require(not (elements - {"mobile_logs"}), "SAMBEE_RECOVERY_DB_MISSING")
        return "FRESH"
    regular(DATA / "sambee.db", 1000)
    db = sqlite3.connect("file:" + str(DATA / "sambee.db") + "?mode=ro", uri=True)
    try:
        db.execute("PRAGMA query_only=ON")
        users = count(db, "user")
        current = db.execute(
            "select count(*) from user where username=?", (username,)
        ).fetchone()[0]
        if users == 1 and current == 1:
            return "OWNER_EXISTS"
        require(not already, "SAMBEE_RECOVERY_REPEATED_LEGACY_ADMIN")
        legacy = db.execute(
            "select count(*) from user where username='admin'"
        ).fetchone()[0]
        require(username != "admin" and users == 1 and legacy == 1,
                "SAMBEE_RECOVERY_FOREIGN_IDENTITY")
        for table in EMPTY_TABLES:
            require(count(db, table) == 0, "SAMBEE_RECOVERY_STATE_NOT_EMPTY_" + table)
        return "LEGACY_EMPTY_ADMIN"
    finally:
        db.close()


def stopped():
    check = subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE), "ps", "-q", "sambee"],
        capture_output=True, timeout=15, check=True, text=True)
    container = check.stdout.strip()
    require(bool(re.fullmatch(r"[0-9a-f]{12,64}", container)),
            "SAMBEE_RECOVERY_CONTAINER_NOT_FOUND")
    state = subprocess.run(
        ["docker", "inspect", "--format", "{{.State.Running}}", container],
        capture_output=True, timeout=15, check=True, text=True)
    require(state.stdout.strip() == "false",
            "SAMBEE_RECOVERY_CONTAINER_MUST_BE_STOPPED")


def syncdir(path):
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def apply(username):
    require(inspect(username) == "LEGACY_EMPTY_ADMIN",
            "SAMBEE_RECOVERY_NOT_REQUIRED")
    stopped()
    if SNAPSHOTS.exists() or SNAPSHOTS.is_symlink():
        directory(SNAPSHOTS, 0)
        require(stat.S_IMODE(SNAPSHOTS.stat().st_mode) == 0o700,
                "SAMBEE_RECOVERY_BACKUP_DIR_UNSAFE")
        require(not list(SNAPSHOTS.iterdir()),
                "SAMBEE_RECOVERY_PREVIOUS_SNAPSHOT_EXISTS")
    else:
        SNAPSHOTS.mkdir(mode=0o700)
        syncdir(BASE)
    label = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = SNAPSHOTS / ("before-owner-" + label + "-" + str(os.getpid()))
    target.mkdir(mode=0o700)
    syncdir(SNAPSHOTS)
    # Both paths are on the same guest filesystem. os.replace is atomic:
    # the prior complete database and WAL stay intact inside a root-only dir.
    os.replace(DATA, target / "data")
    syncdir(BASE)
    syncdir(target)
    DATA.mkdir(mode=0o750)
    os.chown(DATA, 1000, 1000)
    syncdir(BASE)
    fd = os.open(MARKER, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w", encoding="ascii") as out:
        json.dump({"schema": 1, "owner": username, "backup": str(target)}, out)
        out.write("\n")
        out.flush()
        os.fsync(out.fileno())
    syncdir(BASE)
    require((target / "data" / "sambee.db").is_file(),
            "SAMBEE_RECOVERY_SNAPSHOT_NOT_VERIFIED")
    require(stat.S_IMODE(MARKER.stat().st_mode) == 0o600,
            "SAMBEE_RECOVERY_MARKER_UNSAFE")
    print("SAMBEE_RECOVERY=BACKUP_SAVED_NEW_DATA_STAGED")


def main():
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--verify-new-owner", action="store_true")
    parser.add_argument("--credentials-file", type=Path, required=True)
    args = parser.parse_args()
    require(os.geteuid() == 0, "SAMBEE_RECOVERY_REQUIRES_ROOT")
    username = owner(args.credentials_file)
    state = inspect(username)
    if args.check:
        if state == "LEGACY_EMPTY_ADMIN":
            print("SAMBEE_RECOVERY=REQUIRED")
        else:
            print("SAMBEE_RECOVERY=NOT_REQUIRED")
    elif args.apply:
        apply(username)
    else:
        require(state == "OWNER_EXISTS", "SAMBEE_OWNER_FIRSTBOOT_NOT_PROVISIONED")
        print("SAMBEE_RECOVERY=OWNER_IDENTIFIED")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError, sqlite3.Error,
            subprocess.SubprocessError, tomllib.TOMLDecodeError) as error:
        print("SAMBEE_RECOVERY=BLOCKED_" + str(error)
              if isinstance(error, Blocked) else
              "SAMBEE_RECOVERY=BLOCKED_RUNTIME", file=sys.stderr)
        sys.exit(2)
