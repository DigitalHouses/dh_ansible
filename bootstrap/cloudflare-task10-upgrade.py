#!/usr/bin/env python3
"""One-time, reviewed, no-service-restart upgrade of accepted Cloudflare executors.

Run as root on site PVE from the pinned, clean dh_ansible checkout. Existing
PVE tokens, cloudflared service and Semaphore history are not modified here.
"""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
PREVIOUS = "67a983e9915138cfde750fe7c4a42e03e54b2525"
FILES = (
    ("bootstrap/cloudflare-pve-cutover.py",
     "/usr/local/sbin/dh-cloudflare-pve-cutover", "host", 0o700),
    ("bootstrap/semaphore-project.py",
     "/opt/digitalhouses/bootstrap/semaphore-project.py", "guest", 0o644),
)


class Block(Exception):
    pass


def require(cond, reason):
    if not cond:
        raise Block(reason)


def run(*argv, timeout=45):
    p = subprocess.run(argv, capture_output=True, timeout=timeout, check=False)
    require(p.returncode == 0, "SUBPROCESS_FAILED_" + Path(argv[0]).name)
    return p.stdout.strip()


def sha(data):
    return hashlib.sha256(data).hexdigest()


def legacy(rel):
    return run("git", "-C", str(ROOT), "show", PREVIOUS + ":" + rel)


def guest_sha(ctid, path, mode):
    snippet = (
        "import hashlib,pathlib,sys;"
        "p=pathlib.Path(sys.argv[1]);"
        "s=p.stat() if p.is_file() and not p.is_symlink() else None;"
        "print(('SAFE:' if s.st_uid==0 and (s.st_mode&0o777)==int(sys.argv[2],8)"
        " else 'UNSAFE:')+hashlib.sha256(p.read_bytes()).hexdigest()"
        " if s else ('UNSAFE' if p.exists() or p.is_symlink() else 'ABSENT'))"
    )
    return run("pct", "exec", str(ctid), "--", "python3", "-c", snippet,
               path, f"{mode:04o}").decode()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--site", required=True)
    p.add_argument("--ctid", type=int, default=1231)
    args = p.parse_args()
    require(os.geteuid() == 0 and Path("/etc/pve/user.cfg").is_file(),
            "PVE_ROOT_REQUIRED")
    require(args.ctid == 1231 and re.fullmatch(
        r"[a-z][a-z0-9]*(?:-[a-z0-9]+)+", args.site), "SITE_INVALID")
    require(run("pct", "exec", str(args.ctid), "--", "hostname")
            == ("ansible-" + args.site).encode(), "RUNNER_IDENTITY_DRIFT")
    require(run("git", "-C", str(ROOT), "status", "--porcelain") == b"",
            "SOURCE_CHECKOUT_DIRTY")
    require(run("git", "-C", str(ROOT), "rev-parse", "HEAD") != PREVIOUS.encode(),
            "NEW_PINNED_RELEASE_REQUIRED")
    operations = []
    for rel, destination, target, mode in FILES:
        path = ROOT / rel
        require(path.is_file() and not path.is_symlink()
                and path.stat().st_uid == 0, "SOURCE_UNTRUSTED")
        new = path.read_bytes()
        prior = legacy(rel)
        if target == "host":
            dest = Path(destination)
            require(dest.is_file() and not dest.is_symlink()
                    and dest.stat().st_uid == 0
                    and (dest.stat().st_mode & 0o777) == mode,
                    "HOST_TARGET_DRIFT")
            actual = sha(dest.read_bytes())
        else:
            val = guest_sha(args.ctid, destination, mode)
            require(val.startswith("SAFE:"), "RUNNER_TARGET_DRIFT")
            actual = val[5:]
        require(actual in (sha(prior), sha(new)),
                "UNKNOWN_INSTALLED_CODE_RELEASE_" + path.name)
        operations.append((path, destination, target, mode, actual, sha(new)))
    print("UPGRADE_PREFLIGHT=PASS")
    for path, dest, target, mode, current_sha, desired_sha in operations:
        if current_sha == desired_sha:
            print("UPGRADE_UNCHANGED=" + path.name)
            continue
        if target == "host":
            item = Path(dest)
            stage = item.with_name(item.name + ".dh-stage")
            require(not stage.exists() and not stage.is_symlink(),
                    "STALE_HOST_STAGE")
            with stage.open("xb") as out:
                out.write(path.read_bytes())
                out.flush()
                os.fsync(out.fileno())
            stage.chmod(mode)
            os.replace(stage, item)
        else:
            stage = dest + ".dh-stage"
            run("pct", "push", str(args.ctid), str(path), stage,
                "--user", "root", "--group", "root", "--perms", f"{mode:04o}")
            snippet = (
                "import hashlib,os,pathlib,sys;"
                "a=pathlib.Path(sys.argv[1]);b=pathlib.Path(sys.argv[2]);"
                "h=lambda p:hashlib.sha256(p.read_bytes()).hexdigest();"
                "assert a.is_file() and b.is_file() and not a.is_symlink()"
                " and not b.is_symlink() and a.stat().st_uid==0"
                " and b.stat().st_uid==0;"
                "assert h(a)==sys.argv[3] and h(b)==sys.argv[4];"
                "os.replace(b,a)"
            )
            run("pct", "exec", str(args.ctid), "--", "python3", "-c",
                snippet, dest, stage, current_sha, desired_sha)
        print("UPGRADE_APPLIED=" + path.name)
    print("CLOUDFLARED=UNCHANGED")
    print("SEMAPHORE_RUNNING_TASKS=UNCHANGED")
    print("CATALOG_00_READY=YES")


if __name__ == "__main__":
    try:
        main()
    except Block as exc:
        print("BLOCKED: " + str(exc))
        sys.exit(2)
    except Exception:
        print("BLOCKED: UNEXPECTED_UPGRADE_ERROR")
        sys.exit(2)
