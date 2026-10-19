#!/usr/bin/env python3
"""Read-only gate before destructive fresh runner E2E on Proxmox VE.

Intentionally refuses to claim deletion/rebuild readiness. Produces facts only.
Never prints credentials, SSH private keys or Semaphore secret material.
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path

CTID = 1231
PRIVATE_GUEST_KEY = "/var/lib/ansimble/.ssh/pve_host_write_ed25519"
PVE_STAGE = Path("/var/lib/digitalhouses/bootstrap")


def run(*args: str) -> tuple[int, str]:
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=15, check=False)
        return p.returncode, p.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return 127, ""


def check(condition: bool, label: str, failure: str):
    print(f"{label}={'PASS' if condition else 'BLOCKED'}")
    if not condition:
        print(f"  REASON={failure}")
    return condition


def parse_pve_config(payload: str) -> dict:
    conf: dict[str, str] = {}
    for line in payload.splitlines():
        if ": " in line:
            key, value = line.split(": ", 1)
            conf[key] = value
    return conf


def audit(args):
    all_ok = True
    root = os.geteuid() == 0 and Path("/etc/pve/user.cfg").exists()
    all_ok &= check(root, "HOST_PVE_ROOT", "run as root on PVE host")
    if not root:
        return False
    all_ok &= check(args.ctid == CTID, "RUNNER_CTID",
                    "fleet runner must have CTID 1231")
    valid_site = bool(re.fullmatch(r"[a-z][a-z0-9_-]{0,30}", args.site))
    all_ok &= check(valid_site, "SITE_ID", "invalid site id")
    try:
        expected = ipaddress.IPv4Interface(args.runner_cidr)
        pve_ip = ipaddress.IPv4Address(args.pve_ip)
        all_ok &= check(expected.ip.packed[-1] == 231, "IP_SCHEME",
                        "runner address must end in .231")
        all_ok &= check(expected.ip != pve_ip, "IP_DISTINCT",
                        "PVE host and runner must have distinct addresses")
    except ValueError:
        print("IP_SCHEME=BLOCKED\n  REASON=invalid IPv4 address")
        return False

    rc, raw = run("pct", "config", str(args.ctid))
    all_ok &= check(rc == 0, "RUNNER_PRESENT",
                    "CT1231 not found: use fresh install flow instead")
    if rc != 0:
        return False
    cfg = parse_pve_config(raw)
    identity_ok = (cfg.get("hostname") == f"ansimble-{args.site}"
                   and cfg.get("unprivileged") == "1"
                   and f"ip={expected}" in cfg.get("net0", "")
                   and "nesting=1" in cfg.get("features", ""))
    all_ok &= check(identity_ok, "RUNNER_IDENTITY",
                    "CT1231 is not the expected unprivileged Ansimble runner")
    disks = [k for k in cfg if re.fullmatch(r"mp\d+|unused\d+|dev\d+|hookscript", k)]
    custom_lxc = [k for k in cfg if k.startswith("lxc.")]
    all_ok &= check(not disks and not custom_lxc, "EXTRA_MOUNTS_DEVICES",
                    "Unexpected mount, unused volume, passthrough or hookscript: "
                    + ",".join(disks + custom_lxc))
    rootfs = cfg.get("rootfs", "")
    all_ok &= check(rootfs.startswith("local-lvm:"), "ROOTFS",
                    "Unexpected container storage target; no blind destroy")
    rc, status = run("pct", "status", str(args.ctid))
    all_ok &= check(rc == 0 and status in ("status: running", "status: stopped"),
                    "RUNNER_STATUS", "Could not read current LXC state")
    rc, qm = run("qm", "status", str(args.ctid))
    all_ok &= check(rc != 0, "VMID_COLLISION",
                    "VM1231 also exists; cannot proceed with runner replacement")
    rc, stores = run("pvesm", "status")
    print("STORAGE_STATUS_BEGIN")
    print("\n".join(line for line in stores.splitlines() if
                    any(name in line.split() for name in ("local", "local-lvm"))))
    print("STORAGE_STATUS_END")
    all_ok &= check(rc == 0 and "local-lvm" in stores and "local" in stores,
                    "PVE_STORAGE", "local template and local-lvm root storage required")
    rc, templates = run("pveam", "list", "local")
    names = sorted(set(re.findall(r"debian-13-standard_[^\s/]+_amd64\.tar\.zst", templates)))
    print(f"DEBIAN13_LOCAL_TEMPLATES={len(names)}")
    for template in names:
        print(f"TEMPLATE={template}")
    all_ok &= check(rc == 0 and bool(names), "DEBIAN13_TEMPLATE",
                    "Debian 13 template missing on local: download before replacement")
    rc, jobs = run("pvesh", "get", "/cluster/backup", "--output-format", "json")
    if rc == 0:
        try:
            parsed = json.loads(jobs)
            refs = [x.get("id", "<unknown>") for x in parsed
                    if str(args.ctid) in str(x.get("vmid", "")).split(",")]
            print(f"BACKUP_JOBS_EXPLICIT_CT1231={len(refs)}")
        except (ValueError, TypeError):
            print("BACKUP_JOBS_EXPLICIT_CT1231=UNKNOWN")
    else:
        print("BACKUP_JOBS_EXPLICIT_CT1231=UNKNOWN")
    stage = PVE_STAGE / f"pve-identities-{args.site}-pending.json"
    secure_stage = (stage.is_file() and not stage.is_symlink() and
                    stage.stat().st_uid == 0 and stat.S_IMODE(stage.stat().st_mode) == 0o600)
    if secure_stage:
        try:
            data = json.loads(stage.read_text())
            secure_stage = (data.get("site_id") == args.site
                            and data.get("runner_ip") == str(expected.ip)
                            and set(data.get("pve_tokens", {})) == {"audit", "write"})
        except (ValueError, TypeError):
            secure_stage = False
    all_ok &= check(secure_stage, "PVE_API_TOKENS_ESCROW",
                    "Both token secrets must be present in root-only PVE staging")
    if status == "status: running":
        rc, key_status = run("pct", "exec", str(args.ctid), "--", "test", "-f", PRIVATE_GUEST_KEY)
        all_ok &= check(rc == 0, "LINUX_SSH_PRIVATE_KEY_IN_CT",
                        "Guest PVE SSH private key missing")
        rc, sem = run("pct", "exec", str(args.ctid), "--", "systemctl", "is-active", "semaphore")
        print(f"SEMAPHORE_SERVICE={'ACTIVE' if rc == 0 and sem == 'active' else 'NOT_ACTIVE'}")
    else:
        print("LINUX_SSH_PRIVATE_KEY_IN_CT=UNKNOWN (CT stopped)")
    rc, backup = run("which", "vzdump")
    all_ok &= check(rc == 0, "VZDUMP_AVAILABLE",
                    "Proxmox native backup executable required for rollback")
    print("SENSITIVE_CONTENT_PRINTED=NO")
    print("DESTRUCTIVE_ACTION=NONE")
    print("BACKUP_REQUIRED=YES")
    print("KEY_RECONCILIATION_REQUIRED=YES")
    print("VAULT_IMPORT_PENDING=YES")
    print(f"READ_ONLY_PREFLIGHT={'PASS' if all_ok else 'BLOCKED'}")
    print("DELETE_AUTHORIZATION_NOT_IMPLIED=YES")
    return all_ok


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site", required=True)
    parser.add_argument("--ctid", type=int, default=CTID)
    parser.add_argument("--runner-cidr", required=True)
    parser.add_argument("--pve-ip", required=True)
    args = parser.parse_args()
    if not audit(args):
        sys.exit(2)


if __name__ == "__main__":
    main()
