#!/usr/bin/env python3
"""Run one pinned Semaphore catalog playbook via the existing site runner.

This is a root-invoked *unprivileged playbook launcher*, not a privileged
worker, credential broker, API fallback, release publisher, or permissions grant.
Its execution requires an already authorized remote terminal and tool access.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import pwd
import re
import socket
import stat
import subprocess
import sys
import uuid

SITE_RE = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)+\Z")
SHA_RE = re.compile(r"[0-9a-f]{40}\Z")
PLAYBOOK_RE = re.compile(r"playbooks/[a-z0-9-]+\.yml\Z")
ROOT = Path("/var/lib/semaphore")
SITE_DATA = Path("/var/lib/ansible/site-data")
ANSIBLE = Path("/opt/ansible/venv/bin/ansible-playbook")
WORKER_VERIFY = Path("/usr/local/sbin/dh-pve-media-release-verify")
WORKER_WATCHER = "dh-pve-access-audit.path"
WORKER_SERVICE = "dh-pve-access-audit.service"
WORKER_REQUEST = Path("/var/lib/semaphore/dh-pve-access-audit.request")
LOCK_PATH = Path("/run/dh-ansible-task-runner.lock")
LOG_DIR = Path("/var/log/dh-ansible-direct")
CANONICAL_EXECUTABLE = Path(
    "/opt/digitalhouses/bootstrap/pve-access/scripts/dh-task-runner.py")


class Blocked(Exception):
    pass


def require(condition: bool, label: str) -> None:
    if not condition:
        raise Blocked(label)


def run_read(*args: str) -> str:
    try:
        completed = subprocess.run(args, check=True, capture_output=True,
                                   text=True, timeout=20)
    except (subprocess.CalledProcessError, OSError, subprocess.TimeoutExpired):
        raise Blocked("PIN_OR_RUNTIME_PREFLIGHT_FAILED") from None
    return completed.stdout.strip()


def secure_regular(path: Path) -> None:
    require(path.is_file() and not path.is_symlink(), "UNSAFE_FILE")


def resolve_task(catalog: dict, task_id: str) -> dict:
    require(isinstance(catalog, dict) and catalog.get("schema") == 1 and
            isinstance(catalog.get("tasks"), list), "CATALOG_SCHEMA_INVALID")
    items = catalog["tasks"]
    require(bool(items), "CATALOG_EMPTY")
    titles = [item.get("title") for item in items if isinstance(item, dict)]
    require(len(titles) == len(items) and
            all(isinstance(title, str) and
                re.fullmatch(r"[0-9]{2}-[^\r\n]{3,90}", title)
                for title in titles) and
            len(set(titles)) == len(titles), "CATALOG_DUPLICATE_OR_INVALID")
    if re.fullmatch(r"[0-9]{2}", task_id):
        selected = [item for item in items if item["title"].startswith(task_id + "-")]
    else:
        selected = [item for item in items if item["title"] == task_id]
    require(len(selected) == 1, "TASK_NOT_UNIQUE_OR_NOT_IN_CATALOG")
    item = selected[0]
    require(isinstance(item.get("playbook"), str) and
            PLAYBOOK_RE.fullmatch(item["playbook"]) is not None,
            "TASK_PLAYBOOK_PATH_INVALID")
    require(set(item).issubset({"title", "playbook", "view", "survey_vars"}),
            "CATALOG_TASK_FIELDS_INVALID")
    return item


def parse_survey(definitions: object, values: list[str]) -> list[str]:
    specs = definitions if definitions is not None else []
    require(isinstance(specs, list), "SURVEY_SCHEMA_INVALID")
    received: dict[str, str] = {}
    for raw in values:
        require("=" in raw, "SURVEY_FORMAT_INVALID")
        name, value = raw.split("=", 1)
        require(name not in received and re.fullmatch(r"[a-z][a-z0-9_]{2,63}", name)
                is not None, "SURVEY_DUPLICATE_OR_INVALID_NAME")
        received[name] = value
    allow: dict[str, set[str]] = {}
    for spec in specs:
        require(isinstance(spec, dict) and spec.get("type") == "enum" and
                spec.get("required") is True and
                isinstance(spec.get("name"), str) and
                isinstance(spec.get("values"), list), "SURVEY_SCHEMA_INVALID")
        name = spec["name"]
        options = spec["values"]
        require(name not in allow and len(options) >= 2 and
                all(isinstance(o, dict) and isinstance(o.get("value"), str) and
                    re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", o["value"])
                    for o in options), "SURVEY_OPTIONS_INVALID")
        allow[name] = {o["value"] for o in options}
    require(set(received) == set(allow), "SURVEY_REQUIRED_OR_UNEXPECTED")
    for name, value in received.items():
        require(value in allow[name], "SURVEY_VALUE_NOT_ALLOWED")
    return [f"{name}={received[name]}" for name in sorted(received)]


def verified_checkout(path: Path, expected_sha: str) -> Path:
    require(SHA_RE.fullmatch(expected_sha) is not None, "COMMIT_INVALID")
    require(path.is_absolute() and path.parent == ROOT and
            re.fullmatch(r"dh-(?:media-direct|task)-[0-9a-f]{7}", path.name)
            is not None, "CHECKOUT_PATH_NOT_APPROVED")
    require(path.name.endswith(expected_sha[:7]), "CHECKOUT_NAME_MISMATCH")
    require(path.is_dir() and not path.is_symlink(), "CHECKOUT_MISSING_OR_SYMLINK")
    semaphore = pwd.getpwnam("semaphore")
    require(path.stat().st_uid == semaphore.pw_uid, "CHECKOUT_OWNER_INVALID")
    require(run_read("runuser", "-u", "semaphore", "--", "git", "-C",
                     str(path), "rev-parse", "HEAD") == expected_sha,
            "CHECKOUT_SHA_MISMATCH")
    require(run_read("runuser", "-u", "semaphore", "--", "git", "-C",
                     str(path), "status", "--porcelain") == "",
            "CHECKOUT_DIRTY")
    return path


def task_uses_protected_worker(playbook: Path) -> bool:
    secure_regular(playbook)
    require(playbook.stat().st_size <= 200_000, "PLAYBOOK_OVERSIZE")
    body = playbook.read_text(encoding="utf-8")
    # No per-task IDs or application exceptions. A playbook requesting the
    # fixed-purpose PVE media worker must match its pinned asset release.
    return "dh-pve-access-audit." in body or "dh-pve-access-audit.request" in body


def verify_worker(expected_sha: str) -> None:
    require(run_read("python3", str(WORKER_VERIFY), "--expected-commit", expected_sha)
            .startswith("MEDIA_WORKER_RELEASE=PASS FILES="),
            "MEDIA_WORKER_NOT_PINNED")
    require(run_read("systemctl", "is-active", WORKER_WATCHER) == "active",
            "WORKER_WATCHER_INACTIVE")
    require(not WORKER_REQUEST.exists(), "WORKER_REQUEST_PENDING")
    active = subprocess.run(["systemctl", "is-active", "--quiet", WORKER_SERVICE],
                            capture_output=True, timeout=10)
    require(active.returncode != 0, "WORKER_BUSY")


def active_ansible_sessions() -> bool:
    for proc in Path("/proc").iterdir():
        if not proc.name.isdecimal() or int(proc.name) == os.getpid():
            continue
        try:
            args = (proc / "cmdline").read_bytes().split(b"\0")
        except (OSError, PermissionError):
            continue
        if any(arg and Path(os.fsdecode(arg)).name == "ansible-playbook"
               for arg in args):
            return True
    return False


def secure_site(site: str) -> Path:
    require(SITE_RE.fullmatch(site) is not None, "SITE_ID_INVALID")
    require(socket.gethostname().split(".", 1)[0] == "ansible-" + site,
            "RUNNER_SITE_IDENTITY_MISMATCH")
    folder = SITE_DATA / site
    require(folder.is_dir() and not folder.is_symlink(), "SITE_INVENTORY_MISSING")
    semaphore = pwd.getpwnam("semaphore")
    for relative in ("hosts.yml", "group_vars/all.yml"):
        p = folder / relative
        secure_regular(p)
        info = p.stat()
        require(info.st_uid == 0 and info.st_gid == semaphore.pw_gid and
                stat.S_IMODE(info.st_mode) == 0o640,
                "INVENTORY_OWNERSHIP_INVALID")
    return folder / "hosts.yml"


def command_for(checkout: Path, inventory: Path, entry: dict,
                survey: list[str]) -> list[str]:
    args = ["runuser", "-u", "semaphore", "--", "env", "-i",
            "HOME=/var/lib/semaphore", "USER=semaphore", "LOGNAME=semaphore",
            "LANG=C.UTF-8", "LC_ALL=C.UTF-8",
            "PATH=/usr/local/bin:/usr/bin:/bin",
            "ANSIBLE_ROLES_PATH=" + str(checkout / "roles"),
            str(ANSIBLE), "-i", str(inventory), entry["playbook"]]
    for value in survey:
        args.extend(["-e", value])
    return args


def check_trusted_launcher() -> None:
    # Executing a mutable clone owned by semaphore as root would let an
    # unprivileged process change the launcher. Use only the root-owned
    # fixed-purpose release staged and SHA-verified at bootstrap.
    path = Path(__file__)
    require(path == CANONICAL_EXECUTABLE and path.is_file() and
            not path.is_symlink(), "LAUNCHER_NOT_ROOT_STAGED")
    info = path.stat()
    require(info.st_uid == 0 and not (info.st_mode & 0o022),
            "LAUNCHER_OWNER_UNSAFE")


def cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site", required=True)
    parser.add_argument("--task", required=True, help="Exact catalog title or 2-digit ID")
    parser.add_argument("--commit", required=True, help="Exact 40-character Git SHA")
    parser.add_argument("--checkout", required=True, type=Path)
    parser.add_argument("--survey", action="append", default=[], help="catalog enum name=value")
    parser.add_argument("--execute", action="store_true", help="Actually run after preflight")
    args = parser.parse_args(argv)
    require(os.geteuid() == 0, "ROOT_OWNED_RUNNER_SESSION_REQUIRED")
    check_trusted_launcher()
    require(ANSIBLE.is_file(), "ANSIBLE_NOT_INSTALLED")
    inv = secure_site(args.site)
    checkout = verified_checkout(args.checkout, args.commit)
    catalog_path = checkout / "catalog/semaphore-tasks.json"
    secure_regular(catalog_path)
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    entry = resolve_task(catalog, args.task)
    extras = parse_survey(entry.get("survey_vars"), args.survey)
    source = checkout / entry["playbook"]
    secure_regular(source)
    require(source.resolve().is_relative_to(checkout.resolve()),
            "PLAYBOOK_ESCAPE")
    pinned = task_uses_protected_worker(source)
    if pinned:
        verify_worker(args.commit)
    print("SITE=" + args.site, flush=True)
    print("TASK=" + entry["title"], flush=True)
    print("CHECKOUT_SHA=" + args.commit, flush=True)
    print("WORKER_PIN=" + ("PASS" if pinned else "NOT_REQUIRED_BY_PLAYBOOK"), flush=True)
    print("SURVEY=" + (",".join(extras) or "NONE"), flush=True)
    if not args.execute:
        print("RESULT=PLAN_ONLY; use --execute for an explicitly approved run", flush=True)
        return 0
    # The lock covers the full playbook. No parallel direct executions on a
    # shared site runner; no privilege or worker-credential delegation.
    lock_fd = os.open(LOCK_PATH, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(lock_fd, "r+b") as lock:
        require(os.fstat(lock.fileno()).st_uid == 0,
                "LOCK_OWNER_UNSAFE")
        os.fchmod(lock.fileno(), 0o600)
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        require(not active_ansible_sessions(), "OTHER_ANSIBLE_RUNNING")
        if pinned:
            verify_worker(args.commit)
        LOG_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
        require(not LOG_DIR.is_symlink() and LOG_DIR.stat().st_uid == 0 and
                stat.S_IMODE(LOG_DIR.stat().st_mode) == 0o700,
                "LOG_DIR_UNSAFE")
        task_prefix = entry["title"][:2]
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        log_path = LOG_DIR / f"{args.site}-{task_prefix}-{stamp}-{uuid.uuid4().hex[:8]}.log"
        print("METHOD=DIRECT_SEMAPHORE_USER", flush=True)
        print("LOG=" + str(log_path), flush=True)
        fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "wb") as log:
                completed = subprocess.run(command_for(checkout, inv, entry, extras),
                                           cwd=checkout, stdout=log,
                                           stderr=subprocess.STDOUT, check=False)
        except BaseException:
            print("RESULT=UNKNOWN; inspect root-only log", flush=True)
            raise
        print("EXIT_STATUS=" + str(completed.returncode), flush=True)
        print("RESULT=" + ("PLAYBOOK_EXIT_ZERO" if completed.returncode == 0
                           else "PLAYBOOK_FAILED") + "; verify postconditions separately",
              flush=True)
        return completed.returncode if completed.returncode >= 0 else 128 - completed.returncode


if __name__ == "__main__":
    try:
        sys.exit(cli())
    except (Blocked, OSError, ValueError, json.JSONDecodeError) as error:
        code = str(error) if isinstance(error, Blocked) else "RUNNER_PREFLIGHT_ERROR"
        print("BLOCKED=" + code, file=sys.stderr)
        sys.exit(2)
