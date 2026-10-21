#!/opt/ansible/venv/bin/python
"""Fixed-purpose CT root worker: an approved Semaphore request invokes one PVE host cutover."""
from __future__ import annotations

import os
from pathlib import Path
import pwd
import grp
import re
import stat
import subprocess
import sys
import tempfile

import yaml

REQUEST = Path("/var/lib/semaphore/dh-cloudflare-switch.request")
STATUS = Path("/run/dh-cloudflare-switch.status")
SITE_BASE = Path("/var/lib/ansible/site-data")
UUID = re.compile(r"SWITCH:([0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})\Z")
HOSTNAME = re.compile(r"ansible-([a-z][a-z0-9]*(?:-[a-z0-9]+)+)\Z")


class Block(Exception):
    pass


def require(ok, why):
    if not ok:
        raise Block(why)


def guarded_file(path, uid=0, mode=None):
    require(path.is_file() and not path.is_symlink(), "FILE_MISSING_OR_SYMLINK")
    s = path.stat()
    require(s.st_uid == uid and (mode is None or stat.S_IMODE(s.st_mode) == mode),
            "UNSAFE_FILE_OWNERSHIP_OR_MODE")
    return path


def report(nonce, state):
    require(state in ("PASS", "FAILED"), "INVALID_STATUS")
    require(not STATUS.is_symlink(), "STATUS_SYMLINK")
    fd, name = tempfile.mkstemp(prefix="dh-cf-switch-", dir="/run")
    try:
        with os.fdopen(fd, "w") as out:
            out.write(nonce + "|" + state + "\n")
            out.flush()
            os.fsync(out.fileno())
        os.chown(name, 0, grp.getgrnam("semaphore").gr_gid)
        os.chmod(name, 0o640)
        os.replace(name, STATUS)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def main():
    require(os.geteuid() == 0, "ROOT_REQUIRED")
    user = pwd.getpwnam("semaphore")
    guarded_file(REQUEST, user.pw_uid, 0o600)
    require(REQUEST.stat().st_size <= 64, "REQUEST_TOO_LARGE")
    m = UUID.fullmatch(REQUEST.read_text().strip())
    require(m is not None, "INVALID_REQUEST")
    nonce = m.group(1)
    REQUEST.unlink()
    try:
        hm = HOSTNAME.fullmatch(os.uname().nodename)
        require(hm is not None, "INVALID_LOCAL_HOSTNAME")
        site_id = hm.group(1)
        root = SITE_BASE / site_id
        require(root.is_dir() and not root.is_symlink(), "SITE_INVENTORY_ABSENT")
        site_path = guarded_file(root / "group_vars/all.yml")
        pve_path = guarded_file(root / "pve.yml")
        access_path = guarded_file(root / "pve_access.yml")
        site = yaml.safe_load(site_path.read_text())["site"]
        pve = yaml.safe_load(pve_path.read_text())["pve"]
        access = yaml.safe_load(access_path.read_text())["pve_access"]
        require(site["id"] == site_id and site["id"] ==
                site["owner_slug"] + "-" + site["type"], "SITE_IDENTITY_MISMATCH")
        host = str(pve["host"])
        ssh = access["host"]
        require(ssh["user"] == "ansible-linux" and isinstance(ssh["source_ipv4"], str)
                and ssh["ssh_port"] == 22, "HOST_ACCESS_DRIFT")
        require(re.fullmatch(r"192\.168\.\d{1,3}\.\d{1,3}", host),
                "PVE_IP_INVALID")
        key = guarded_file(Path(ssh["identity_file"]), mode=0o600)
        known = guarded_file(Path(ssh["known_hosts_file"]), mode=0o600)
        command = [
            "/usr/bin/ssh", "-T", "-o", "BatchMode=yes",
            "-o", "IdentitiesOnly=yes", "-o", "StrictHostKeyChecking=yes",
            "-o", "UserKnownHostsFile=" + str(known),
            "-o", "ConnectTimeout=8",
            "-i", str(key),
            ssh["user"] + "@" + host,
            "sudo", "-n", "/usr/local/sbin/dh-cloudflare-pve-cutover", "--apply",
        ]
        result = subprocess.run(command, capture_output=True, text=True,
                                timeout=1000, check=False)
        require(result.returncode == 0
                and ("CF_CUTOVER=PASS" in result.stdout
                     or "CF_CUTOVER=ALREADY_APPLIED" in result.stdout)
                and "TUNNEL_HEALTH=PASS" in result.stdout, "PVE_CUTOVER_FAILED")
        report(nonce, "PASS")
    except Exception:
        report(nonce, "FAILED")
        raise


if __name__ == "__main__":
    try:
        main()
    except Block as e:
        print("BLOCKED: " + str(e))
        sys.exit(2)
    except Exception:
        print("BLOCKED: RUNNER_CUTOVER_FAILED")
        sys.exit(2)
