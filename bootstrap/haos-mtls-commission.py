#!/usr/bin/env python3
"""One-time root bootstrap for Semaphore task 70 (not an extra Semaphore task).

Run only from an reviewed, SHA-pinned dh_ansible checkout, separately on the
authorized site runner and its PVE. This must never be invoked by Semaphore.
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys

HERE = Path(__file__).resolve().parent
SITE = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)+\Z")
UUID = re.compile(r"[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}\Z")
PVE_CFG = Path("/etc/digitalhouses/cloudflare/site.json")
HA_CFG = Path("/etc/digitalhouses/cloudflare/haos-mtls.json")
SECRETS = Path("/etc/digitalhouses/cloudflare/haos-mtls")
RUNNER_BIN = Path("/usr/local/sbin/dh-haos-mtls-runner")
PVE_BIN = Path("/usr/local/sbin/dh-cloudflare-pve-haos-mtls")
UNIT_PATH = Path("/etc/systemd/system/dh-haos-mtls.path")
UNIT_SERVICE = Path("/etc/systemd/system/dh-haos-mtls.service")
SUDOERS = Path("/etc/sudoers.d/dh-cloudflare-haos-mtls")


class Block(Exception):
    pass


def require(ok, msg):
    if not ok:
        raise Block(msg)


def run(*argv):
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=25, check=False)
    require(proc.returncode == 0, "COMMAND_FAILED")
    return proc.stdout.strip()


def root_text(path):
    require(path.is_file() and not path.is_symlink(), "CONFIG_MISSING_OR_LINKED")
    info = path.stat()
    require(info.st_uid == 0 and stat.S_IMODE(info.st_mode) == 0o600,
            "CONFIG_PERMISSIONS")
    return path.read_text()


def install_file(source, target, mode):
    require(source.is_file() and not source.is_symlink(), "SOURCE_MISSING")
    require(source.resolve().is_relative_to(HERE), "SOURCE_NOT_FROM_BOOTSTRAP")
    require(not target.is_symlink(), "DESTINATION_SYMLINK")
    content = source.read_bytes()
    if target.exists():
        require(target.is_file() and target.stat().st_uid == 0
                and stat.S_IMODE(target.stat().st_mode) == mode
                and target.read_bytes() == content,
                "EXISTING_INSTALL_DIFFERS_REVIEW_REQUIRED")
        return False
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                 | getattr(os, "O_NOFOLLOW", 0), mode)
    with os.fdopen(fd, "wb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    os.chmod(target, mode)
    return True


def write_new(path, data, mode=0o600):
    require(not path.is_symlink(), "NEW_CONFIG_SYMLINK")
    if path.exists():
        require(path.is_file() and path.read_bytes() == data and
                stat.S_IMODE(path.stat().st_mode) == mode,
                "EXISTING_CONFIG_DIFFERS_REVIEW_REQUIRED")
        return False
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                 | getattr(os, "O_NOFOLLOW", 0), mode)
    with os.fdopen(fd, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    os.chmod(path, mode)
    return True


def approved_checkout(expected):
    require(re.fullmatch(r"[a-f0-9]{40}", expected or ""), "EXPECTED_SHA_REQUIRED")
    root = HERE.parent
    require(run("git", "-C", str(root), "rev-parse", "HEAD") == expected,
            "PUBLIC_CODE_SHA_NOT_PINNED")
    require(not run("git", "-C", str(root), "status", "--porcelain",
                    "--untracked-files=all"), "GIT_WORKTREE_NOT_CLEAN")
    # No unreviewed remote shell downloads during commissioning.


def runner():
    hm = re.fullmatch(r"ansible-([a-z][a-z0-9]*(?:-[a-z0-9]+)+)",
                      os.uname().nodename)
    require(hm is not None, "NOT_CANONICAL_RUNNER")
    site = hm.group(1)
    data = Path("/var/lib/ansible/site-data") / site
    require((data / "haos.yml").is_file() and (data / "group_vars/all.yml").is_file(),
            "SITE_DATA_MISSING")
    require(Path("/var/lib/semaphore").is_dir(), "SEMAPHORE_ABSENT")
    import pwd
    require(Path("/var/lib/semaphore").stat().st_uid == pwd.getpwnam("semaphore").pw_uid,
            "SEMAPHORE_DATA_DRIFT")
    for src, dst, mode in (
        ("dh-haos-mtls-runner.py", RUNNER_BIN, 0o700),
        ("dh-haos-mtls.path", UNIT_PATH, 0o644),
        ("dh-haos-mtls.service", UNIT_SERVICE, 0o644),
    ):
        install_file(HERE / src, dst, mode)
    run("systemctl", "daemon-reload")
    run("systemctl", "enable", "--now", "dh-haos-mtls.path")
    require(run("systemctl", "is-active", "dh-haos-mtls.path") == "active",
            "RUNNER_WATCHER_NOT_ACTIVE")
    print("TASK70_RUNNER_COMMISSION=PASS")


def pve(args):
    cf = json.loads(root_text(PVE_CFG))
    site = cf.get("site_id", "")
    require(SITE.fullmatch(site), "CANONICAL_PVE_ID_INVALID")
    require(cf.get("zone") == "digitalhouses.vip" and
            UUID.fullmatch(str(cf.get("new_tunnel_id", ""))),
            "PVE_CANONICAL_TUNNEL_MISSING")
    require(args.vmid is not None and 100 <= args.vmid <= 9999, "VMID_REQUIRED")
    require(args.ipv4 is not None, "HAOS_IP_REQUIRED")
    ip = ipaddress.ip_address(args.ipv4)
    require(ip.version == 4 and ip.is_private and str(ip).startswith("192.168."),
            "HAOS_ADDRESS_NOT_LAN")
    require("running" in run("/usr/sbin/qm", "status", str(args.vmid)),
            "HAOS_VM_NOT_RUNNING")
    require(not SECRETS.is_symlink(), "MATERIAL_SYMLINK")
    if not SECRETS.exists():
        SECRETS.mkdir(mode=0o700)
    require(SECRETS.is_dir() and SECRETS.stat().st_uid == 0
            and stat.S_IMODE(SECRETS.stat().st_mode) == 0o700,
            "MATERIAL_PERMISSIONS_INVALID")
    config = {
        "site_id": site,
        "hostname": site + "-ha-mtls.digitalhouses.vip",
        "vmid": args.vmid,
        "ipv4": str(ip),
        "port": 80,
        "certificate_policy": "one-per-home-shared-with-residents",
        # An extra root-only explicit authorization; private Fleet must
        # independently approve before Semaphore even requests the worker.
        "approved_for_publish": bool(args.approve_publish),
    }
    desired = (json.dumps(config, sort_keys=True, indent=2) + "\n").encode()
    write_new(HA_CFG, desired)
    install_file(HERE / "dh-cloudflare-pve-haos-mtls.py", PVE_BIN, 0o700)
    permission = (
        "ansible-linux ALL=(root) NOPASSWD: " + str(PVE_BIN) + " --apply\n"
    ).encode()
    write_new(SUDOERS, permission, mode=0o440)
    run("visudo", "-cf", str(SUDOERS))
    print("TASK70_PVE_COMMISSION=PASS")
    print("HAOS_MTLS_PUBLICATION_APPROVED=" + str(args.approve_publish).upper())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", required=True, choices=("runner", "pve"))
    parser.add_argument("--expected-public-sha", required=True)
    parser.add_argument("--vmid", type=int)
    parser.add_argument("--ipv4")
    parser.add_argument("--approve-publish", action="store_true")
    args = parser.parse_args()
    require(os.geteuid() == 0, "ROOT_REQUIRED")
    approved_checkout(args.expected_public_sha)
    if args.target == "runner":
        require(args.vmid is None and args.ipv4 is None
                and not args.approve_publish, "RUNNER_TAKES_NO_SITE_ARGS")
        runner()
    else:
        pve(args)


if __name__ == "__main__":
    try:
        main()
    except Block as exc:
        print("BLOCKED: " + str(exc))
        sys.exit(2)
    except Exception:
        print("BLOCKED: COMMISSION_FAILED")
        sys.exit(2)
