#!/usr/bin/env python3
"""One-time owner-initiated installer of fixed Cloudflare cutover executors.

Run on the site's PVE as root FROM AN APPROVED, PINNED dh_ansible checkout.
Does not create/repoint any tunnel, change DNS, install a new connector,
or obtain/store Cloudflare tokens.
"""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
HOST_SOURCE = ROOT / "bootstrap/cloudflare-pve-cutover.py"
RUNNER_SOURCE = ROOT / "bootstrap/cloudflare-runner-cutover.py"
PATH_SOURCE = ROOT / "bootstrap/dh-cloudflare-switch.path"
SERVICE_SOURCE = ROOT / "bootstrap/dh-cloudflare-switch.service"


class Block(Exception):
    pass


def require(condition, label):
    if not condition:
        raise Block(label)


def checked(*command):
    result = subprocess.run(list(command), capture_output=True, text=True, timeout=40)
    require(result.returncode == 0, "COMMAND_FAILED_" + Path(command[0]).name)
    return result.stdout.strip()


def valid_source(path):
    require(path.is_file() and not path.is_symlink(), "SOURCE_MISSING_OR_LINKED")
    st = path.stat()
    require(st.st_uid == 0 and not (st.st_mode & stat.S_IWOTH),
            "SOURCE_UNTRUSTED")
    require(st.st_size < 150000, "SOURCE_UNEXPECTED_SIZE")


def stage_host(source, target, mode):
    valid_source(source)
    require(not target.is_symlink(), "HOST_TARGET_SYMLINK")
    if target.is_file():
        info = target.stat()
        require(info.st_uid == 0 and stat.S_IMODE(info.st_mode) == mode,
                "HOST_TARGET_PERMISSIONS_DRIFT")
        require(target.read_bytes() == source.read_bytes(), "HOST_TARGET_CONFLICT")
        print("HOST_STAGE=UNCHANGED " + target.name)
        return
    require(not target.exists(), "HOST_TARGET_NOT_REGULAR")
    target.parent.mkdir(parents=True, exist_ok=True)
    # Creation is exclusive: never silently replace a locally customized unit.
    fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, mode)
    with os.fdopen(fd, "wb") as handle:
        handle.write(source.read_bytes())
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(target, mode)
    print("HOST_STAGE=CREATED " + target.name)


def stage_guest(ctid, source, target, mode):
    valid_source(source)
    # Existing target must match: no silent replacement of root-owned code.
    existing = subprocess.run(
        ["pct", "exec", str(ctid), "--", "python3", "-c",
         "import hashlib,pathlib,sys;"
         "p=pathlib.Path(sys.argv[1]);"
         "s=p.stat() if p.is_file() and not p.is_symlink() else None;"
         "print(('SAFE:' if s.st_uid==0 and (s.st_mode&0o777)==int(sys.argv[2],8) else 'UNSAFE:')+hashlib.sha256(p.read_bytes()).hexdigest() if s else ('UNSAFE' if p.exists() or p.is_symlink() else 'ABSENT'))",
         target, f"{mode:04o}"], capture_output=True, text=True, timeout=25
    )
    require(existing.returncode == 0, "GUEST_TARGET_AUDIT_FAILED")
    value = existing.stdout.strip()
    expected = hashlib.sha256(source.read_bytes()).hexdigest()
    if value == "SAFE:" + expected:
        print("GUEST_STAGE=UNCHANGED " + Path(target).name)
        return
    require(value == "ABSENT", "GUEST_TARGET_CONFLICT")
    checked("pct", "push", str(ctid), str(source), target,
            "--user", "root", "--group", "root", "--perms", f"{mode:04o}")
    print("GUEST_STAGE=CREATED " + Path(target).name)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--site", required=True)
    parser.add_argument("--ctid", type=int, default=1231)
    args = parser.parse_args()
    require(os.geteuid() == 0 and Path("/etc/pve/user.cfg").is_file(),
            "ROOT_ON_PVE_REQUIRED")
    require(bool(re.fullmatch(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)+", args.site)),
            "SITE_ID_INVALID")
    require(args.ctid == 1231, "RUNNER_CTID_DRIFT")
    require(checked("pct", "status", str(args.ctid)) == "status: running",
            "RUNNER_NOT_RUNNING")
    require(checked("pct", "exec", str(args.ctid), "--", "hostname")
            == "ansible-" + args.site, "RUNNER_IDENTITY_DRIFT")
    require(checked("systemctl", "is-active", "cloudflared.service")
            == "active", "EXISTING_CLOUDFLARED_INACTIVE")
    for source in (HOST_SOURCE, RUNNER_SOURCE, PATH_SOURCE, SERVICE_SOURCE):
        valid_source(source)
    host_target = Path("/usr/local/sbin/dh-cloudflare-pve-cutover")
    stage_host(HOST_SOURCE, host_target, 0o700)
    stage_guest(args.ctid, RUNNER_SOURCE,
                "/usr/local/sbin/dh-cloudflare-runner-cutover", 0o700)
    stage_guest(args.ctid, PATH_SOURCE,
                "/etc/systemd/system/dh-cloudflare-switch.path", 0o644)
    stage_guest(args.ctid, SERVICE_SOURCE,
                "/etc/systemd/system/dh-cloudflare-switch.service", 0o644)
    checked("pct", "exec", str(args.ctid), "--", "systemctl", "daemon-reload")
    checked("pct", "exec", str(args.ctid), "--", "systemctl", "enable",
            "--now", "dh-cloudflare-switch.path")
    require(checked("pct", "exec", str(args.ctid), "--", "systemctl",
                    "is-active", "dh-cloudflare-switch.path") == "active",
            "RUNNER_WATCHER_NOT_READY")
    print("CF_RUNNER_WATCHER=ACTIVE")
    print("CF_PVE_HOST_EXECUTOR=STAGED")
    print("CF_CONNECTOR=UNCHANGED")
    print("CF_API_TOKEN=NOT_REQUESTED")
    print("CF_COMMISSION=PASS")


if __name__ == "__main__":
    try:
        main()
    except Block as e:
        print("BLOCKED: " + str(e))
        sys.exit(2)
    except Exception:
        print("BLOCKED: COMMISSIONING_FAILED_NO_SECRET_OUTPUT")
        sys.exit(2)
