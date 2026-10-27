#!/usr/bin/env python3
"""Reconcile app bootstrap from one existing owner SMB password; never rotate it."""
import argparse
import grp
import os
from pathlib import Path
import re
import stat
import subprocess
import sys

IDENTITY = re.compile(r"^[a-z][a-z0-9_]{2,30}$")
ALPHABET = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789")


def check(value, reason):
    if not value:
        raise RuntimeError(reason)


def read_owner(root: Path, username: str):
    check(root.is_absolute() and root.name == "digitalhouses" and
          root.parent.name.endswith("-pool") and
          IDENTITY.fullmatch(username) is not None,
          "OWNER_IDENTITY_INVALID")
    file = root / "credentials" / (username + ".txt")
    check(not file.is_symlink(), "OWNER_PASSPORT_SYMLINK")
    info = file.lstat()
    admin_gid = grp.getgrnam("admin_group").gr_gid
    check(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and
          info.st_gid in (0, admin_gid) and
          stat.S_IMODE(info.st_mode) in (0o600, 0o640) and
          info.st_nlink == 1, "OWNER_PASSPORT_UNSAFE")
    data = file.read_text(encoding="ascii").splitlines()
    check(len(data) == 2 and data[0] == "username=" + username and
          data[1].startswith("password="), "OWNER_PASSPORT_INVALID")
    password = data[1].removeprefix("password=")
    check(len(password) == 8 and all(c in ALPHABET for c in password),
          "OWNER_PASSWORD_POLICY_INVALID")
    return password


def filestash_admin(password):
    """This sets the Filestash *configuration admin* hash; file login uses SMB."""
    dest = Path("/etc/digitalhouses/filestash.env")
    check(dest.parent.is_dir() and not dest.parent.is_symlink(),
          "FILESTASH_CONFIG_DIRECTORY_UNAVAILABLE")
    check(os.stat(dest.parent).st_uid == 0, "FILESTASH_DIRECTORY_NOT_ROOT")
    if dest.exists() or dest.is_symlink():
        info = dest.lstat()
        check(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and
              stat.S_IMODE(info.st_mode) == 0o600 and
              info.st_nlink == 1, "FILESTASH_ADMIN_ENV_UNSAFE")
        content = dest.read_text("ascii").strip()
        check(content.startswith("ADMIN_PASSWORD=$2") and
              content.count("\n") == 0, "FILESTASH_ADMIN_ENV_INVALID")
        # Credential changes are handled by an explicit authenticated rotation.
        # A repeat Ansible run never changes a previously commissioned hash.
        return False
    result = subprocess.run(["/usr/bin/htpasswd", "-niB", "-C", "12", ""],
                            input=(password + "\n").encode("ascii"),
                            capture_output=True, check=True)
    output = result.stdout.decode("ascii").strip()
    check(":" in output and output.split(":", 1)[1].startswith("$2"),
          "FILESTASH_BCRYPT_INVALID")
    fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                 getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "w", encoding="ascii") as file:
        file.write("ADMIN_PASSWORD=" + output.split(":", 1)[1] + "\n")
        file.flush()
        os.fsync(file.fileno())
    return True


def provision(root, owner):
    check(os.geteuid() == 0, "ROOT_REQUIRED")
    fstype = subprocess.run(["findmnt", "-n", "-o", "FSTYPE", "-T", str(root)],
                            capture_output=True, text=True, check=True)
    check(fstype.stdout.strip() == "zfs", "OWNER_SECRET_NOT_ON_ZFS")
    password = read_owner(root, owner)
    updated = filestash_admin(password)
    print("MEDIA_SHARED_OWNER_CREDENTIAL=VERIFIED")
    print("FILESTASH_ADMIN_ENV=" + ("CREATED" if updated else "UNCHANGED"))
    print("MEDIA_APP_CREDENTIALS=READY")


if __name__ == "__main__":
    try:
        parser = argparse.ArgumentParser()
        parser.add_argument("--digitalhouses-root", type=Path, required=True)
        parser.add_argument("--owner", required=True)
        args = parser.parse_args()
        provision(args.digitalhouses_root, args.owner)
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError):
        print("MEDIA_APP_CREDENTIALS=BLOCKED", file=sys.stderr)
        sys.exit(2)
