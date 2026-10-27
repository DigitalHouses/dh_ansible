#!/usr/bin/env python3
"""Generate reusable application admin credentials in existing DigitalHouses ZFS."""
import argparse
import grp
import os
from pathlib import Path
import pwd
import secrets
import stat
import string
import subprocess
import sys

ALPHABET = string.ascii_letters + string.digits
APPS = ("filestash", "jellyfin", "qbittorrent")


def check(value, message):
    if not value:
        raise RuntimeError(message)


def safe_dir(path, group=None):
    if not path.exists():
        path.mkdir(mode=0o700)
    st = path.lstat()
    check(stat.S_ISDIR(st.st_mode) and st.st_uid == 0 and
          st.st_gid in ((0, group) if group is not None else (0,)) and
          not path.is_symlink(), "CREDENTIAL_DIRECTORY_UNSAFE")
    if group is not None:
        os.chown(path, 0, group)
    path.chmod(0o750 if group is not None else 0o700)


def atomic_create(path, text, group=None):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                 getattr(os, "O_NOFOLLOW", 0), 0o600)
    if group is not None:
        os.fchown(fd, 0, group)
        os.fchmod(fd, 0o640)
    with os.fdopen(fd, "w", encoding="ascii") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())


def account(directory, app, group=None):
    path = directory / (app + ".txt")
    if path.exists() or path.is_symlink():
        st = path.lstat()
        check(stat.S_ISREG(st.st_mode) and st.st_uid == 0 and
              stat.S_IMODE(st.st_mode) in ((0o600, 0o640) if group is not None else (0o600,)) and
              st.st_gid in ((0, group) if group is not None else (0,)) and
              st.st_nlink == 1, "ACCOUNT_FILE_UNSAFE")
        if group is not None:
            os.chown(path, 0, group)
            path.chmod(0o640)
        parts = path.read_text("ascii").splitlines()
        check(len(parts) == 2 and parts[0] == "username=serzhan_admin" and
              parts[1].startswith("password="), "ACCOUNT_FILE_INVALID")
        password = parts[1][9:]
        check(len(password) == 24 and all(ch in ALPHABET for ch in password),
              "ACCOUNT_PASSWORD_INVALID")
        return password, False
    password = "".join(secrets.choice(ALPHABET) for _ in range(24))
    atomic_create(path, "username=serzhan_admin\npassword=" + password + "\n", group)
    return password, True


def configure_filestash(directory, password):
    import tempfile
    result = subprocess.run(
        ["/usr/bin/htpasswd", "-niB", "-C", "12", ""],
        input=(password + "\n").encode("ascii"),
        capture_output=True, check=True)
    line = result.stdout.decode("ascii").strip()
    check(":" in line and line.split(":", 1)[1].startswith("$2"),
          "FILESTASH_HASH_INVALID")
    hash_value = line.split(":", 1)[1]
    target = Path("/etc/digitalhouses/filestash.env")
    safe_dir(target.parent)
    desired = ("ADMIN_PASSWORD=" + hash_value + "\n").encode("ascii")
    if target.exists():
        st = target.lstat()
        check(stat.S_ISREG(st.st_mode) and st.st_uid == 0 and
              stat.S_IMODE(st.st_mode) == 0o600, "FILESTASH_ENV_UNSAFE")
        # Existing bcrypt hash may have a random salt; preserve it on rerun.
        if target.read_bytes().startswith(b"ADMIN_PASSWORD=$2"):
            return False
        raise RuntimeError("FILESTASH_ENV_CONFLICT")
    atomic_create(target, desired.decode("ascii"))
    return True


def provision(root):
    check(os.geteuid() == 0 and root.is_absolute() and
          root.name == "digitalhouses" and root.parent.name.endswith("-pool"),
          "APP_CREDENTIAL_ROOT_INVALID")
    fstype = subprocess.run(["findmnt", "-n", "-o", "FSTYPE", "-T", str(root)],
                            capture_output=True, text=True, check=True)
    check(fstype.stdout.strip() == "zfs", "APP_CREDENTIALS_NOT_ON_ZFS")
    admin_gid = grp.getgrnam("admin_group").gr_gid
    parent = root / "credentials"
    safe_dir(parent, admin_gid)
    directory = parent / "apps"
    safe_dir(directory, admin_gid)
    for app in APPS:
        password, created = account(directory, app, admin_gid)
        if app == "filestash":
            changed = configure_filestash(directory, password)
            check(changed or not created, "FILESTASH_ENV_ALREADY_EXISTS_NEW_PASSWORD")
        print("MEDIA_APP_CREDENTIAL_" + app.upper() + "=" +
              ("CREATED" if created else "UNCHANGED"))
    print("MEDIA_APP_CREDENTIALS=READY")


if __name__ == "__main__":
    try:
        parser = argparse.ArgumentParser()
        parser.add_argument("--digitalhouses-root", type=Path, required=True)
        provision(parser.parse_args().digitalhouses_root)
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as e:
        print("MEDIA_APP_CREDENTIALS=BLOCKED_" + type(e).__name__, file=sys.stderr)
        sys.exit(2)
