#!/usr/bin/env python3
"""Read the current media owner credential only for a privileged PVE root console.

No password is accepted in argv, no network listener, no logfile and no
Semaphore/Ansible dependency. QEMU Guest Agent executes this as root in VM.
"""
import argparse
import json
import os
from pathlib import Path
import re
import stat
import sys

MANIFEST = Path("/etc/digitalhouses/media-password-rotation.json")
NAME = re.compile(r"^[a-z][a-z0-9_]{2,30}$")
POOL = re.compile(r"^[a-z][a-z0-9_-]{1,62}$")


def secure_file(path, accepted):
    st = path.lstat()
    if not stat.S_ISREG(st.st_mode) or st.st_uid != 0 or st.st_nlink != 1:
        raise ValueError("UNSAFE_OWNER_FILE")
    if stat.S_IMODE(st.st_mode) not in accepted:
        raise ValueError("UNSAFE_OWNER_MODE")


def read_owner():
    secure_file(MANIFEST, {0o600})
    data = json.loads(MANIFEST.read_text(encoding="ascii"))
    owner, pool = data["owner"], data["pool"]
    if not NAME.fullmatch(owner) or not POOL.fullmatch(pool):
        raise ValueError("OWNER_IDENTITY_INVALID")
    path = Path("/") / pool / "digitalhouses" / "credentials" / (owner + ".txt")
    secure_file(path, {0o600, 0o640})
    if path.stat().st_size > 512:
        raise ValueError("OWNER_RECORD_OVERSIZE")
    lines = path.read_text(encoding="ascii").splitlines()
    if len(lines) != 2 or lines[0] != "username=" + owner:
        raise ValueError("OWNER_RECORD_INVALID")
    if not lines[1].startswith("password="):
        raise ValueError("OWNER_RECORD_INVALID")
    password = lines[1][9:]
    if not re.fullmatch("[A-Za-z0-9]{6,8}", password):
        raise ValueError("OWNER_RECORD_POLICY_MISMATCH")
    return owner, password


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise PermissionError("ROOT_REQUIRED")
    user, password = read_owner()
    if args.check:
        print("OWNER_PASSWORD=AVAILABLE")
    else:
        print("username=" + user)
        print("password=" + password)


if __name__ == "__main__":
    try:
        main()
    except (OSError, KeyError, ValueError, PermissionError, UnicodeError):
        print("OWNER_PASSWORD=UNAVAILABLE", file=sys.stderr)
        sys.exit(2)
