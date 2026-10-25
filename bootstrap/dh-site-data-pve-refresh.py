#!/usr/bin/env python3
"""PVE-side, owner-credentialed, site-only Fleet inventory reconciliation.

This helper MUST run as root on the actual PVE host. It never reads or
downloads Fleet Vault, only one allowlisted non-secret component YAML.
Installation/credential commissioning is separate from invoking task 80.
"""
from __future__ import annotations

import argparse
import fcntl
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request

REPO = "DigitalHouses/dh_ansible_fleet_detail"
RUNNER_CID = 1231
SITE_RE = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)+\Z")
COMPONENTS = frozenset(("media_vm", "netalertx", "haos"))
SHA_RE = re.compile(r"[a-f0-9]{40}\Z")
ROOT = Path("/var/lib/ansible/site-data")
LIMIT = 131_072


class Blocked(RuntimeError):
    pass


def require(ok: bool, why: str):
    if not ok:
        raise Blocked(why)


def run(*cmd: str, timeout: int = 30) -> str:
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    require(p.returncode == 0, "HOST_OPERATION_FAILED")
    return p.stdout.strip()


def pct(*args: str) -> str:
    return run("/usr/sbin/pct", *args)


def authenticate() -> str:
    """Reuse the GitHub CLI device login from the canonical bootstrap."""
    gh = "/usr/bin/gh"
    require(Path(gh).is_file(), "GH_CLI_NOT_INSTALLED")
    env = os.environ.copy()
    env.pop("GH_TOKEN", None)
    env.pop("GITHUB_TOKEN", None)
    env["HOME"] = "/root"
    env["GH_CONFIG_DIR"] = "/root/.config/gh"
    process = subprocess.run(
        [gh, "auth", "token", "--hostname", "github.com"],
        capture_output=True, text=True, timeout=15, check=False, env=env,
    )
    require(process.returncode == 0, "GH_LOGIN_REQUIRED")
    value = process.stdout.strip()
    require(8 < len(value) < 512 and "\n" not in value and "\r" not in value,
            "GH_AUTH_SESSION_INVALID")
    return value

def api(path: str, token: str) -> dict:
    url = "https://api.github.com/" + path
    request = urllib.request.Request(url, headers={
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "DigitalHouses-Site-Inventory-Reconciler",
    })
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            require(response.status == 200, "GITHUB_STATUS_INVALID")
            raw = response.read(180_000)
            require(len(raw) < 180_000, "GITHUB_RESPONSE_TOO_LARGE")
            return json.loads(raw)
    except (urllib.error.URLError, json.JSONDecodeError, ValueError) as exc:
        raise Blocked("FLEET_GITHUB_FETCH_FAILED") from exc


def pinned_source(site: str, component: str, token: str) -> tuple[bytes, str]:
    branch = api(f"repos/{REPO}/git/ref/heads/main", token)
    sha = branch.get("object", {}).get("sha", "")
    require(bool(SHA_RE.fullmatch(sha)), "FLEET_REF_INVALID")
    file = api(f"repos/{REPO}/contents/inventories/{site}/{component}.yml?ref={sha}", token)
    require(file.get("type") == "file" and file.get("encoding") == "base64",
            "FLEET_FILE_TYPE_BLOCKED")
    require(0 < file.get("size", 0) < LIMIT, "FLEET_FILE_SIZE_BLOCKED")
    raw = base64.b64decode(file.get("content", ""), validate=False)
    require(len(raw) == file["size"], "FLEET_FILE_SIZE_MISMATCH")
    blob = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
    require(blob == file.get("sha"), "FLEET_BLOB_SHA_MISMATCH")
    require(raw.startswith(b"---\n") and b"\x00" not in raw, "FLEET_YAML_ENVELOPE_INVALID")
    return raw, sha


def pct_exists(path: str) -> bool:
    rc = subprocess.run(["/usr/sbin/pct", "exec", str(RUNNER_CID), "--",
                         "/usr/bin/test", "-f", path], timeout=10).returncode
    require(rc in (0, 1), "RUNNER_FILE_TEST_FAILED")
    return rc == 0


def pct_hash(path: str) -> str | None:
    if not pct_exists(path):
        return None
    out = pct("exec", str(RUNNER_CID), "--", "sha256sum", path)
    return out.split()[0]


def pct_file_guard(path: str, *, group: str = "semaphore", mode: str = "640"):
    is_link = subprocess.run(["/usr/sbin/pct", "exec", str(RUNNER_CID), "--",
                              "test", "-L", path], timeout=10).returncode
    require(is_link == 1, "EXISTING_SITE_FILE_SYMLINK_BLOCKED")
    info = pct("exec", str(RUNNER_CID), "--", "stat", "-c", "%U:%G:%a:%F", path)
    require(info == f"root:{group}:{mode}:regular file",
            "EXISTING_SITE_FILE_PERMISSIONS_DRIFT")


def push_atomic(raw: bytes, target: str, *, group: str, mode: str):
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(prefix="dh-site-", dir="/run", delete=False) as f:
            tmp = f.name
            f.write(raw)
            f.flush()
            os.fchmod(f.fileno(), 0o600)
        remote = "/root/dh-site-stage-" + os.path.basename(tmp)
        pct("push", str(RUNNER_CID), tmp, remote)
        expected = hashlib.sha256(raw).hexdigest()
        require(pct_hash(remote) == expected, "SITE_PUSH_SHA_MISMATCH")
        staged = target + ".dh-staged"
        # Fail if leftover staging from an interrupted run; never adopt unknown data.
        require(not pct_exists(staged), "SITE_STALE_STAGE_BLOCKED")
        staged_link = subprocess.run(["/usr/sbin/pct", "exec", str(RUNNER_CID), "--",
                                       "test", "-L", staged], timeout=10).returncode
        require(staged_link == 1, "SITE_STALE_SYMLINK_BLOCKED")
        pct("exec", str(RUNNER_CID), "--", "install", "-o", "root", "-g", group,
            "-m", mode, remote, staged)
        require(pct_hash(staged) == expected, "SITE_INSTALL_SHA_MISMATCH")
        pct("exec", str(RUNNER_CID), "--", "mv", "-T", staged, target)
        require(pct_hash(target) == expected, "SITE_FINAL_SHA_MISMATCH")
    finally:
        if tmp:
            os.unlink(tmp)
            # Remove only the random, root-owned transient staging file.
            if "remote" in locals() and pct_exists(remote):
                require(pct_hash(remote) == hashlib.sha256(raw).hexdigest(),
                        "REMOTE_STAGE_CONTENT_CHANGED")
                pct("exec", str(RUNNER_CID), "--", "rm", "-f", remote)


def reconcile(site: str, component: str):
    require(os.geteuid() == 0 and Path("/etc/pve/user.cfg").exists(),
            "PVE_ROOT_REQUIRED")
    require(SITE_RE.fullmatch(site) is not None, "SITE_INVALID")
    require(component in COMPONENTS, "COMPONENT_NOT_ALLOWED")
    lockfd = os.open("/run/dh-site-data-pve.lock", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(lockfd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise Blocked("SITE_DATA_PROVIDER_BUSY") from exc
        _reconcile_locked(site, component)
    finally:
        os.close(lockfd)


def _reconcile_locked(site: str, component: str):
    require(pct("status", str(RUNNER_CID)) == "status: running", "RUNNER_NOT_RUNNING")
    require(pct("exec", str(RUNNER_CID), "--", "hostname") == "ansible-" + site,
            "RUNNER_SITE_MISMATCH")
    location = f"{ROOT}/{site}"
    require(pct_exists(location + "/hosts.yml"), "SITE_INVENTORY_MISSING")
    check = pct("exec", str(RUNNER_CID), "--", "stat", "-c", "%U:%G:%a:%F", location)
    require(check == "root:semaphore:750:directory", "SITE_DIRECTORY_DRIFT")
    token = authenticate()
    data, sha = pinned_source(site, component, token)
    del token
    target = f"{location}/{component}.yml"
    checksum = hashlib.sha256(data).hexdigest()
    rev_dir = f"/var/lib/ansible/.site-revisions/{site}"
    revision = rev_dir + "/" + component + ".sha256"
    old = pct_hash(target)
    prior = pct_hash(revision)
    if old is not None:
        pct_file_guard(target)
        if prior is not None:
            pct_file_guard(revision, group="root", mode="600")
            last_accepted = pct("exec", str(RUNNER_CID), "--", "cat", revision)
            require(last_accepted == old or old == checksum,
                    "LOCAL_SITE_DATA_DRIFT_BLOCKED")
        else:
            require(old == checksum, "UNTRACKED_SITE_DATA_DRIFT_BLOCKED")
    if old != checksum:
        push_atomic(data, target, group="semaphore", mode="0640")
        pct_file_guard(target)
        print("SITE_DATA_RESULT=CHANGED")
    else:
        print("SITE_DATA_RESULT=UNCHANGED")
    # Record the previously verified hash, allowing future automatic updates
    # only if a subsequent run finds that hash unchanged on disk.
    if prior is None or pct("exec", str(RUNNER_CID), "--", "cat", revision) != checksum:
        pct("exec", str(RUNNER_CID), "--", "install", "-d", "-o", "root",
            "-g", "root", "-m", "0700", "/var/lib/ansible/.site-revisions")
        pct("exec", str(RUNNER_CID), "--", "install", "-d", "-o", "root",
            "-g", "root", "-m", "0700", rev_dir)
        push_atomic((checksum + "\n").encode(), revision, group="root", mode="0600")
        pct_file_guard(revision, group="root", mode="600")
    print("SITE_DATA_SOURCE_SHA=" + sha)
    print("SITE_DATA=PASS")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--site", required=True)
    parser.add_argument("--component", choices=sorted(COMPONENTS), required=True)
    args = parser.parse_args()
    reconcile(args.site, args.component)


if __name__ == "__main__":
    try:
        main()
    except (Blocked, subprocess.TimeoutExpired) as exc:
        print("BLOCKED: " + (str(exc) if isinstance(exc, Blocked) else "OPERATION_TIMEOUT"),
              file=sys.stderr)
        sys.exit(2)
