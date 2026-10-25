#!/usr/bin/env python3
"""One-time owner-authorized commissioning of reusable Semaphore site-data support.

Run on PVE as root from a clean, reviewed immutable dh_ansible checkout.
This never provisions media VM, changes PCI, or touches guest data.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone


CTID = "1231"
SITE = re.compile(r"[a-z][a-z0-9-]{1,40}\Z")
SHA = re.compile(r"[0-9a-f]{40}\Z")

PVE_FILES = (("bootstrap/dh-site-data-pve-refresh.py", "/usr/local/sbin/dh-site-data-pve-refresh"),)
GUEST_FILES = (
    ("bootstrap/pve-access-audit-request.sh", "/usr/local/sbin/dh-pve-access-audit"),
    ("bootstrap/dh-pve-access-audit.service", "/etc/systemd/system/dh-pve-access-audit.service"),
    ("bootstrap/dh-pve-access-audit.path", "/etc/systemd/system/dh-pve-access-audit.path"),
    ("playbooks/audit-pve-access.yml", "/opt/digitalhouses/bootstrap/pve-access/playbooks/audit-pve-access.yml"),
    ("roles/pve_audit/tasks/main.yml", "/opt/digitalhouses/bootstrap/pve-access/roles/pve_audit/tasks/main.yml"),
    ("roles/pve_audit/defaults/main.yml", "/opt/digitalhouses/bootstrap/pve-access/roles/pve_audit/defaults/main.yml"),
    ("roles/pve_media_vm_plan/tasks/main.yml", "/opt/digitalhouses/bootstrap/pve-access/roles/pve_media_vm_plan/tasks/main.yml"),
    ("bootstrap/dh-site-data-runner.py", "/usr/local/sbin/dh-site-data-runner"),
    ("bootstrap/dh-site-data.service", "/etc/systemd/system/dh-site-data.service"),
    ("bootstrap/dh-site-data.path", "/etc/systemd/system/dh-site-data.path"),
)


class Blocked(RuntimeError):
    pass


def call(*argv: str, timeout: int = 35) -> str:
    p = subprocess.run(argv, text=True, capture_output=True, timeout=timeout, check=False)
    if p.returncode:
        raise Blocked(f"OPERATION_FAILED: {argv[0]} rc={p.returncode}")
    return p.stdout.strip()


def pct(*argv: str) -> str:
    return call("/usr/sbin/pct", *argv)


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_checkout(root: Path, commit: str) -> None:
    if not SHA.fullmatch(commit):
        raise Blocked("MISSING_PINNED_COMMIT")
    if call("git", "-C", str(root), "rev-parse", "HEAD") != commit:
        raise Blocked("SOURCE_COMMIT_MISMATCH")
    if call("git", "-C", str(root), "status", "--porcelain") != "":
        raise Blocked("SOURCE_TREE_NOT_CLEAN")
    for src, _ in PVE_FILES:
        f = root / src
        if not f.is_file() or f.is_symlink():
            raise Blocked("SOURCE_FILE_MISSING")
    for src, _ in GUEST_FILES:
        f = root / src
        if not f.is_file() or f.is_symlink():
            raise Blocked("SOURCE_FILE_MISSING")


def verify_site(site: str) -> None:
    if not SITE.fullmatch(site):
        raise Blocked("SITE_ID_INVALID")
    if os.geteuid() != 0 or not Path("/etc/pve/user.cfg").is_file():
        raise Blocked("PVE_ROOT_REQUIRED")
    if pct("status", CTID) != "status: running":
        raise Blocked("RUNNER_NOT_RUNNING")
    if pct("exec", CTID, "--", "hostname") != "ansible-" + site:
        raise Blocked("RUNNER_SITE_MISMATCH")
    if "hostname: ansible-" + site not in pct("config", CTID).splitlines():
        raise Blocked("PVE_RUNNER_IDENTITY_MISMATCH")


def backup_existing(root: Path, site: str):
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = Path("/var/lib/digitalhouses/bootstrap") / ("site-data-rollback-" + site + "-" + stamp)
    backup.mkdir(mode=0o700, parents=True, exist_ok=False)
    os.chmod(backup, 0o700)
    saved = 0
    for source, path in PVE_FILES:
        target = Path(path)
        if target.is_file() and not target.is_symlink():
            dest = backup / ("pve_" + target.name)
            shutil.copy2(target, dest)
            os.chmod(dest, 0o600)
            saved += 1
    for source, target in GUEST_FILES:
        probe = subprocess.run(["/usr/sbin/pct", "exec", CTID, "--", "test", "-f", target],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
        if probe.returncode not in (0, 1):
            raise Blocked("GUEST_BACKUP_PROBE_FAILED")
        if probe.returncode == 0:
            dest = backup / ("guest_" + source.replace("/", "__"))
            pct("pull", CTID, target, str(dest))
            os.chmod(dest, 0o600)
            saved += 1
    print("ROLLBACK_DIR=" + str(backup))
    print("ROLLBACK_FILES=" + str(saved))


def verify_live(root: Path):
    for source, path in PVE_FILES:
        dest = Path(path)
        if file_hash(root / source) != file_hash(dest):
            raise Blocked("PVE_WORKER_HASH_MISMATCH")
    for source, target in GUEST_FILES:
        want = file_hash(root / source)
        got = pct("exec", CTID, "--", "sha256sum", target).split()[0]
        if want != got:
            raise Blocked("RUNNER_FILE_HASH_MISMATCH")
    if pct("exec", CTID, "--", "systemctl", "is-active", "dh-site-data.path") != "active":
        raise Blocked("SITE_DATA_WATCHER_INACTIVE")
    if pct("exec", CTID, "--", "systemctl", "is-active", "dh-pve-access-audit.path") != "active":
        raise Blocked("PVE_AUDIT_WATCHER_INACTIVE")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--site", required=True)
    parser.add_argument("--commit", required=True, help="Exact reviewed public source commit SHA")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    verify_checkout(root, args.commit)
    verify_site(args.site)
    if not args.apply:
        print("SITE_DATA_COMMISSION=PLAN_ONLY")
        print("CHANGES=ROOT_HELPER_AND_EXISTING_PVE_AUDIT_WORKER")
        print("RUNNER_FILES=" + str(len(GUEST_FILES)))
        return
    backup_existing(root, args.site)
    source = root / "bootstrap/semaphore-host-install.py"
    spec = importlib.util.spec_from_file_location("dh_pve_bootstrap", source)
    bootstrap = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bootstrap)
    bootstrap.stage_pve_access_audit(root)
    bootstrap.stage_site_data_provider(root)
    verify_live(root)
    print("SITE_DATA_COMMISSION=PASS")
    print("SOURCE_COMMIT=" + args.commit)
    print("FLEET_READER=REQUIRES_OWNER_GITHUB_AUTH")


if __name__ == "__main__":
    try:
        main()
    except (Blocked, subprocess.TimeoutExpired) as exc:
        print("COMMISSION=BLOCKED: " + str(exc), file=sys.stderr)
        sys.exit(2)
