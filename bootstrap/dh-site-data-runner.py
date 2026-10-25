#!/opt/ansible/venv/bin/python
"""Root-only site-data provider triggered by one Semaphore request.

The runner never holds a private Fleet repository credential. The existing
verified SSH identity delegates a bounded request to an owner-controlled
PVE helper. No caller-provided URL, host, path, command, or Git ref.
"""
from __future__ import annotations

import grp
import os
from pathlib import Path
import pwd
import re
import stat
import subprocess
import sys
import tempfile

import yaml

REQUEST = Path("/var/lib/semaphore/dh-site-data.request")
STATUS = Path("/run/dh-site-data.status")
BASE = Path("/var/lib/ansible/site-data")
SITE = re.compile(r"ansible-([a-z][a-z0-9]*(?:-[a-z0-9]+)+)\Z")
NONCE = re.compile(r"SITE_DATA:([0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}):(media_vm|netalertx|haos)\Z")
KEY = Path("/var/lib/ansible/.ssh/pve_host_write_ed25519")
KNOWN = Path("/var/lib/ansible/.ssh/pve_host_audit_known_hosts")


class Blocked(RuntimeError):
    pass


def require(value, message):
    if not value:
        raise Blocked(message)


def guarded(path: Path, *, owner=0, mode=None):
    require(path.is_file() and not path.is_symlink(), "FILE_MISSING_OR_SYMLINK")
    info = path.stat()
    require(info.st_uid == owner and (mode is None or
            stat.S_IMODE(info.st_mode) == mode), "UNSAFE_FILE_OWNER_OR_MODE")
    return path


def status(nonce: str, result: str):
    group = grp.getgrnam("semaphore").gr_gid
    require(not STATUS.is_symlink(), "STATUS_IS_SYMLINK")
    fd, name = tempfile.mkstemp(prefix="dh-site-data-", dir="/run")
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(nonce + "|" + result + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chown(name, 0, group)
        os.chmod(name, 0o640)
        os.replace(name, STATUS)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def main():
    require(os.geteuid() == 0, "ROOT_REQUIRED")
    user = pwd.getpwnam("semaphore")
    request = guarded(REQUEST, owner=user.pw_uid, mode=0o600)
    require(0 < request.stat().st_size < 100, "REQUEST_SIZE_INVALID")
    match = NONCE.fullmatch(request.read_text().strip())
    require(match is not None, "REQUEST_INVALID")
    nonce, component = match.groups()
    request.unlink()
    try:
        match = SITE.fullmatch(os.uname().nodename)
        require(match is not None, "RUNNER_HOSTNAME_INVALID")
        site_id = match.group(1)
        path = BASE / site_id
        require(path.is_dir() and not path.is_symlink(), "SITE_DIRECTORY_UNSAFE")
        meta = yaml.safe_load(guarded(path / "group_vars/all.yml").read_text())["site"]
        pve = yaml.safe_load(guarded(path / "pve.yml").read_text())["pve"]
        access = yaml.safe_load(guarded(path / "pve_access.yml").read_text())["pve_access"]
        require(meta["id"] == site_id == meta["owner_slug"] + "-" + meta["type"],
                "SITE_IDENTITY_DRIFT")
        require(access["host"]["user"] == "ansible-linux" and
                access["host"]["ssh_port"] == 22, "PVE_SSH_IDENTITY_DRIFT")
        require(pve["node"] and pve["host"], "PVE_ENDPOINT_MISSING")
        guarded(KEY, mode=0o600)
        guarded(KNOWN, mode=0o600)
        cmd = [
            "/usr/bin/ssh", "-T",
            "-o", "BatchMode=yes",
            "-o", "IdentitiesOnly=yes",
            "-o", "StrictHostKeyChecking=yes",
            "-o", "UserKnownHostsFile=" + str(KNOWN),
            "-o", "ConnectTimeout=8",
            "-i", str(KEY),
            "ansible-linux@" + str(pve["host"]),
            "sudo", "-n", "/usr/local/sbin/dh-site-data-pve-refresh",
            "--site", site_id, "--component", component,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True,
                                timeout=180, check=False)
        require(result.returncode == 0, "PVE_SITE_DATA_PROVIDER_FAILED")
        out = result.stdout.splitlines()
        require("SITE_DATA=PASS" in out, "UNVERIFIED_PVE_SITE_DATA_RESULT")
        states = [x.split("=", 1)[1] for x in out if x.startswith("SITE_DATA_RESULT=")]
        require(len(states) == 1 and states[0] in ("CHANGED", "UNCHANGED"),
                "INVALID_SITE_DATA_STATE")
        require(guarded(path / (component + ".yml")).stat().st_gid ==
                grp.getgrnam("semaphore").gr_gid, "SITE_DATA_POSTCONDITION_FAILED")
        status(nonce, "PASS|" + states[0])
    except Exception:
        status(nonce, "FAILED")
        raise


if __name__ == "__main__":
    try:
        main()
    except Blocked as exc:
        print("BLOCKED: " + str(exc), file=sys.stderr)
        sys.exit(2)
    except Exception:
        print("BLOCKED: SITE_DATA_WORKER_FAILED", file=sys.stderr)
        sys.exit(2)
