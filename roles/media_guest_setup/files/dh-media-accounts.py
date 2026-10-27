#!/usr/bin/env python3
"""DigitalHouses: idempotent local credential store and Samba password sync.

Passwords are created once on the approved ZFS digitalhouses dataset and are
never logged, sent over command-line arguments or written to public Git.
"""
import argparse
import grp
import hashlib
import os
from pathlib import Path
import re
import secrets
import stat
import string
import subprocess
import sys

ALPHABET = string.ascii_letters + string.digits
USERNAME = re.compile(r"^[a-z][a-z0-9_]{2,30}$")


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def run(argv, **kwargs):
    return subprocess.run(argv, check=True, **kwargs)


def protected_directory(path, group=None):
    path = Path(path)
    if not path.exists():
        path.mkdir(mode=0o700)
    info = path.lstat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and
            not path.is_symlink() and
            info.st_gid in ((0, group) if group is not None else (0,)),
            "CREDENTIAL_DIRECTORY_UNSAFE")
    if group is not None:
        os.chown(path, 0, group)
    path.chmod(0o750 if group is not None else 0o700)


def read_or_create(path, username, group=None):
    if path.exists() or path.is_symlink():
        info = path.lstat()
        require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and
                stat.S_IMODE(info.st_mode) in ((0o600, 0o640) if group is not None else (0o600,)) and
                info.st_gid in ((0, group) if group is not None else (0,)) and
                info.st_nlink == 1,
                "CREDENTIAL_FILE_UNSAFE")
        if group is not None:
            os.chown(path, 0, group)
            path.chmod(0o640)
        parts = path.read_text(encoding="ascii").splitlines()
        require(len(parts) == 2 and parts[0] == "username=" + username and
                parts[1].startswith("password="), "CREDENTIAL_FILE_INVALID")
        password = parts[1][9:]
        require(len(password) == 8 and all(c in ALPHABET for c in password),
                "CREDENTIAL_PASSWORD_INVALID")
        return password, False
    password = "".join(secrets.choice(ALPHABET) for _ in range(8))
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                 getattr(os, "O_NOFOLLOW", 0), 0o600)
    if group is not None:
        os.fchown(fd, 0, group)
        os.fchmod(fd, 0o640)
    with os.fdopen(fd, "w", encoding="ascii") as file:
        file.write("username=" + username + "\npassword=" + password + "\n")
        file.flush()
        os.fsync(file.fileno())
    return password, True


def provision(root, accounts):
    require(os.geteuid() == 0, "ROOT_ONLY")
    require(root.is_absolute() and root.name == "digitalhouses" and
            root.parent.name.endswith("-pool"), "DIGITALHOUSES_DATASET_PATH_INVALID")
    result = run(["findmnt", "-n", "-o", "FSTYPE", "-T", str(root)],
                 capture_output=True, text=True)
    require(result.stdout.strip() == "zfs", "DIGITALHOUSES_NOT_ON_ZFS")
    admin_gid = grp.getgrnam("admin_group").gr_gid
    protected_directory(root / "credentials", admin_gid)
    state = root / "credentials" / ".samba-sync"
    protected_directory(state)
    for username in accounts:
        require(USERNAME.fullmatch(username) is not None, "ACCOUNT_NAME_INVALID")
        record = root / "credentials" / (username + ".txt")
        password, created = read_or_create(record, username, admin_gid)
        digest = hashlib.sha256((username + "\0" + password).encode("ascii")).hexdigest()
        stamp = state / (username + ".sha256")
        old = stamp.read_text().strip() if stamp.is_file() else None
        exists = subprocess.run(["pdbedit", "-L", "-u", username],
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL).returncode == 0
        if created or digest != old or not exists:
            run(["smbpasswd", "-s", "-a", username],
                input=(password + "\n" + password + "\n").encode("ascii"),
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            tmp = state / (username + ".pending")
            fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC |
                         getattr(os, "O_NOFOLLOW", 0), 0o600)
            with os.fdopen(fd, "w", encoding="ascii") as file:
                file.write(digest + "\n")
            os.replace(tmp, stamp)
            print("SMB_IDENTITY=" + username + ":SYNCHRONIZED")
        else:
            print("SMB_IDENTITY=" + username + ":UNCHANGED")
    print("MEDIA_CREDENTIALS=READY")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--digitalhouses-root", type=Path, required=True)
    parser.add_argument("--account", action="append", required=True)
    args = parser.parse_args()
    try:
        provision(args.digitalhouses_root, args.account)
    except (RuntimeError, OSError, subprocess.CalledProcessError) as exc:
        print("MEDIA_CREDENTIALS=BLOCKED_" + type(exc).__name__, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
