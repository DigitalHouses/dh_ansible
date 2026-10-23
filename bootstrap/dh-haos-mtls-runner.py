#!/opt/ansible/venv/bin/python
"""Fixed-purpose one-shot Semaphore HAOS mTLS worker; no arbitrary commands."""
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

REQUEST = Path("/var/lib/semaphore/dh-haos-mtls.request")
STATUS = Path("/run/dh-haos-mtls.status")
SITE_BASE = Path("/var/lib/ansible/site-data")
NONCE = re.compile(r"HAOS_MTLS:([a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12})\Z")
SITE = re.compile(r"ansible-([a-z][a-z0-9]*(?:-[a-z0-9]+)+)\Z")


class Block(Exception):
    pass


def require(ok, message):
    if not ok:
        raise Block(message)


def guarded(path, uid=0, mode=None):
    require(path.is_file() and not path.is_symlink(), "FILE_MISSING_OR_SYMLINK")
    meta = path.stat()
    require(meta.st_uid == uid and (mode is None or stat.S_IMODE(meta.st_mode) == mode),
            "UNSAFE_OWNER_OR_MODE")
    return path


def report(nonce, outcome):
    require(outcome in ("CHANGED", "UNCHANGED", "FAILED"), "INVALID_STATUS")
    require(not STATUS.is_symlink(), "STATUS_IS_SYMLINK")
    fd, path = tempfile.mkstemp(prefix="dh-haos-mtls-", dir="/run")
    try:
        with os.fdopen(fd, "w") as out:
            out.write(nonce + "|" + outcome + "\n")
            out.flush()
            os.fsync(out.fileno())
        os.chown(path, 0, grp.getgrnam("semaphore").gr_gid)
        os.chmod(path, 0o640)
        os.replace(path, STATUS)
    finally:
        if os.path.exists(path):
            os.unlink(path)


def main():
    require(os.geteuid() == 0, "ROOT_REQUIRED")
    sem = pwd.getpwnam("semaphore")
    request = guarded(REQUEST, sem.pw_uid, 0o600)
    require(request.stat().st_size <= 80, "REQUEST_TOO_LARGE")
    match = NONCE.fullmatch(request.read_text().strip())
    require(match is not None, "INVALID_NONCE")
    nonce = match.group(1)
    request.unlink()  # Cannot replay the same request.
    try:
        hm = SITE.fullmatch(os.uname().nodename)
        require(hm is not None, "UNEXPECTED_RUNNER")
        site_id = hm.group(1)
        root = SITE_BASE / site_id
        require(root.is_dir() and not root.is_symlink(), "SITE_DATA_MISSING")
        site = yaml.safe_load(guarded(root / "group_vars/all.yml").read_text())["site"]
        haos = yaml.safe_load(guarded(root / "haos.yml").read_text())["haos"]
        pve = yaml.safe_load(guarded(root / "pve.yml").read_text())["pve"]
        access = yaml.safe_load(guarded(root / "pve_access.yml").read_text())["pve_access"]
        require(site["id"] == site_id == site["owner_slug"] + "-" + site["type"],
                "SITE_IDENTITY_DRIFT")
        cf = haos["cloudflare"]
        require(cf["hostname"] == site_id + "-pve-ha-mtls.digitalhouses.vip"
                and cf["tunnel"] == site_id
                and cf["mtls_required"] is True
                and cf["mtls_method"] == "cloudflare_client_cert_waf"
                and cf["certificate_policy"] == "one-per-home-shared-with-residents"
                and cf["approved_for_publish"] is True, "SITE_APPROVAL_OR_HOSTNAME_DRIFT")
        require(isinstance(haos["vmid"], int) and haos["vmid"] >= 100
                and haos["network"]["port"] == 80, "HAOS_TARGET_DRIFT")
        ssh = access["host"]
        require(ssh["user"] == "ansible-linux" and ssh["ssh_port"] == 22,
                "SSH_POLICY_DRIFT")
        host = str(pve["host"])
        require(re.fullmatch(r"192\.168\.\d{1,3}\.\d{1,3}", host) is not None,
                "PVE_HOST_INVALID")
        key = guarded(Path(ssh["identity_file"]), mode=0o600)
        known = guarded(Path(ssh["known_hosts_file"]), mode=0o600)
        cmd = [
            "/usr/bin/ssh", "-T", "-o", "BatchMode=yes",
            "-o", "IdentitiesOnly=yes", "-o", "StrictHostKeyChecking=yes",
            "-o", "UserKnownHostsFile=" + str(known),
            "-o", "ConnectTimeout=8", "-i", str(key),
            ssh["user"] + "@" + host, "sudo", "-n",
            "/usr/local/sbin/dh-cloudflare-pve-haos-mtls", "--apply",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=240, check=False)
        require(result.returncode == 0, "PVE_HAOS_MTLS_FAILED")
        lines = result.stdout.splitlines()
        require("HAOS_MTLS=PASS" in lines and
                ("HAOS_MTLS_HOSTNAME=" + cf["hostname"]) in lines and
                ("HAOS_MTLS_ORIGIN=http://" + haos["network"]["ipv4"] + ":"
                 + str(haos["network"]["port"])) in lines and
                len([line for line in lines if line.startswith("HAOS_MTLS_RESULT=")]) == 1,
                "PVE_IDENTITY_OR_RESULT_INVALID")
        outcome = next(line.partition("=")[2] for line in lines
                       if line.startswith("HAOS_MTLS_RESULT="))
        require(outcome in ("CHANGED", "UNCHANGED"), "PVE_RESULT_INVALID")
        report(nonce, outcome)
    except Exception:
        report(nonce, "FAILED")
        raise


if __name__ == "__main__":
    try:
        main()
    except Block as err:
        print("BLOCKED: " + str(err))
        sys.exit(2)
    except Exception:
        print("BLOCKED: HAOS_MTLS_WORKER_FAILED")
        sys.exit(2)
