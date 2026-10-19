#!/usr/bin/env python3
"""Fail closed on obviously unsafe content before making DH Ansible public.

This scanner is a defence-in-depth gate. A clean scan is NOT evidence that a
history-wide security review is complete. Never output matching credential text.
"""
from __future__ import annotations

import pathlib
import re
import subprocess
import sys


BLOCKED_PATHS = (
    re.compile(r"^secrets/"),
    re.compile(r"^inventories/(?!example/)[^/]+/"),
    re.compile(r"^docs/baselines/"),
    re.compile(r"^docs/(?:plans|runbooks)/(?:amir|home)[^/]*"),
)
BLOCKED_CONTENT = (
    re.compile(rb"-----BEGIN (?:OPENSSH |RSA |EC )?PRIVATE KEY-----"),
    re.compile(rb"\A\$ANSIBLE_VAULT;"),
    re.compile(rb"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(rb"ghp_[A-Za-z0-9]{25,}"),
    re.compile(rb"AKIA[0-9A-Z]{16}"),
    re.compile(rb"\b192\.168\.\d{1,3}\.\d{1,3}\b"),
    re.compile(rb"\b10\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"),
    re.compile(rb"\b172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}\b"),
    re.compile(rb"/opt/digitalhouses/ansimble-2\.0"),
)


def history_scan(root: pathlib.Path) -> int:
    """Scan every reachable Git blob and historical filename from all refs.

    Safe for a private staging repository: do not print filenames, matched
    credential contents, Git object IDs or any secret-bearing diff fragments.
    """
    def git(*args: str, input_text: str | None = None) -> str:
        p = subprocess.run(
            ["git", "-C", str(root), *args],
            input=input_text, capture_output=True, text=True, check=True,
        )
        return p.stdout

    raw_objects = git("rev-list", "--objects", "--all").splitlines()
    if not raw_objects:
        print("PUBLIC_HISTORY=FAIL_NO_REACHABLE_COMMITS")
        return 1
    object_names = [line.split(" ", 1)[0] for line in raw_objects]
    names = git("log", "--all", "--format=", "--name-only").splitlines()
    sensitive_paths = {
        name for name in names if any(x.search(name) for x in BLOCKED_PATHS)
    }
    meta = git(
        "cat-file", "--batch-check=%(objectname) %(objecttype) %(objectsize)",
        input_text="\n".join(object_names) + "\n",
    )
    sensitive_blobs = 0
    oversize_blobs = 0
    scanned_blobs = 0
    total_bytes = 0
    seen = set()
    for line in meta.splitlines():
        parts = line.split()
        if len(parts) != 3 or not parts[2].isdigit():
            print("PUBLIC_HISTORY=FAIL_INVALID_GIT_OBJECT")
            return 1
        oid, kind, size = parts[0], parts[1], int(parts[2])
        if kind != "blob" or oid in seen:
            continue
        seen.add(oid)
        scanned_blobs += 1
        if size > 4_000_000 or total_bytes + size > 80_000_000:
            oversize_blobs += 1
            continue
        total_bytes += size
        data = subprocess.run(
            ["git", "-C", str(root), "cat-file", "blob", oid],
            capture_output=True, check=True,
        ).stdout
        if len(data) != size:
            print("PUBLIC_HISTORY=FAIL_BLOB_SIZE_MISMATCH")
            return 1
        if any(pattern.search(data) for pattern in BLOCKED_CONTENT):
            sensitive_blobs += 1
    print("PUBLIC_HISTORY_SCANNED_BLOBS=" + str(scanned_blobs))
    print("PUBLIC_HISTORY_SENSITIVE_PATHS=" + str(len(sensitive_paths)))
    print("PUBLIC_HISTORY_SENSITIVE_BLOBS=" + str(sensitive_blobs))
    print("PUBLIC_HISTORY_OVERSIZE_BLOBS=" + str(oversize_blobs))
    if sensitive_paths or sensitive_blobs or oversize_blobs:
        print("PUBLIC_HISTORY=FAIL")
        return 1
    print("PUBLIC_HISTORY=PASS_ALL_REACHABLE_REFS")
    return 0


def main() -> int:
    root = pathlib.Path(__file__).resolve().parents[1]
    if len(sys.argv) == 2 and sys.argv[1] == "--history":
        return history_scan(root)
    if len(sys.argv) != 1:
        print("USAGE=public-boundary-check.py [--history]")
        return 2
    proc = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z"],
        capture_output=True, check=True
    )
    paths = [p.decode("utf-8") for p in proc.stdout.split(b"\0") if p]
    blocked = set()
    for name in paths:
        if any(expr.search(name) for expr in BLOCKED_PATHS):
            blocked.add(name)
        file = root / name
        if not file.is_file() or file.is_symlink():
            blocked.add(name)
            continue
        data = file.read_bytes()
        if any(expr.search(data) for expr in BLOCKED_CONTENT):
            blocked.add(name)
    if blocked:
        print("PUBLIC_BOUNDARY=FAIL")
        for name in sorted(blocked):
            print("REVIEW_REQUIRED_FILE=" + name)
        return 1
    print("PUBLIC_BOUNDARY=PASS_CURRENT_TREE_ONLY")
    print("PUBLIC_HISTORY_AUDIT=STILL_REQUIRED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
