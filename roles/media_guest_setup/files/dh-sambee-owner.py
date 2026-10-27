#!/usr/bin/env python3
"""Commission Sambee local login via upstream endpoints, without touching SQLite."""
import argparse
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
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:8000"
NAME = re.compile(r"^[a-z][a-z0-9_]{2,30}$")
CHARS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789")


def require(valid, reason):
    if not valid:
        raise RuntimeError(reason)


def credential(path):
    require(os.geteuid() == 0 and path.parent.name == "credentials" and
            path.parent.parent.name == "digitalhouses" and
            NAME.fullmatch(path.stem) is not None, "SAM_BEE_OWNER_SCOPE_INVALID")
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and
            stat.S_IMODE(info.st_mode) == 0o640 and
            info.st_nlink == 1, "SAM_BEE_OWNER_FILE_UNSAFE")
    data = path.read_text("ascii").splitlines()
    require(len(data) == 2 and data[0] == "username=" + path.stem and
            data[1].startswith("password=") and
            len(data[1][9:]) == 8 and
            all(c in CHARS for c in data[1][9:]), "SAM_BEE_OWNER_FILE_INVALID")
    return path.stem, data[1][9:]


def request(endpoint, values, bearer=None):
    if bearer:
        body = json.dumps(values).encode("utf-8")
        headers = {"Authorization": "Bearer " + bearer,
                   "Content-Type": "application/json"}
    else:
        body = urllib.parse.urlencode(values).encode("utf-8")
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
    req = urllib.request.Request(BASE + endpoint, data=body, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=12) as response:
            return json.loads(response.read(65536))
    except urllib.error.HTTPError as exc:
        if exc.code == 401 and not bearer:
            return None
        raise RuntimeError("SAM_BEE_HTTP_" + str(exc.code)) from None


def token(username, password):
    result = request("/api/auth/token", {"username": username,
                                         "password": password})
    return result.get("access_token") if isinstance(result, dict) else None


def first_credentials(compose_path):
    run = subprocess.run(
        ["docker", "compose", "-f", str(compose_path),
         "logs", "--no-color", "--tail", "300", "sambee"],
        capture_output=True, text=True, timeout=20, check=True)
    # This output contains a secret and must NEVER be printed or forwarded.
    lines = run.stdout.splitlines() + run.stderr.splitlines()
    blocks = [i for i, l in enumerate(lines)
              if "FIRST-TIME SETUP - SAVE THESE CREDENTIALS" in l]
    require(len(blocks) == 1, "SAM_BEE_BOOTSTRAP_LOG_NOT_UNIQUE")
    block = lines[blocks[0]:blocks[0] + 8]
    usernames = re.findall(r"Username:\s*([A-Za-z0-9_]+)", "\n".join(block))
    passwords = re.findall(r"Password:\s*(\S+)", "\n".join(block))
    require(len(usernames) == 1 and len(passwords) == 1,
            "SAM_BEE_BOOTSTRAP_LOG_UNAVAILABLE")
    return usernames[0], passwords[0]


def stamp_file(path, content):
    fd, tmp = tempfile.mkstemp(prefix=".sambee-password-", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="ascii") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def reconcile(owner_file, compose_path):
    user, password = credential(owner_file)
    stamp = owner_file.parent / ".sambee-owner.sha256"
    sha = hashlib.sha256((user + "\0" + password).encode()).hexdigest()
    if stamp.exists() or stamp.is_symlink():
        info = stamp.lstat()
        require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and
                stat.S_IMODE(info.st_mode) == 0o600 and info.st_nlink == 1,
                "SAM_BEE_STAMP_UNSAFE")
        require(stamp.read_text("ascii").strip() == sha,
                "SAM_BEE_OWNER_ROTATION_NOT_COMMISSIONED")
    if token(user, password):
        if not stamp.exists():
            stamp_file(stamp, sha + "\n")
        print("SAM_BEE_OWNER=READY")
        return
    require(not stamp.exists(), "SAM_BEE_OWNER_CREDENTIAL_DRIFT")
    first_user, first_password = first_credentials(compose_path)
    require(first_user == user, "SAM_BEE_INITIAL_USER_MISMATCH")
    initial_token = token(first_user, first_password)
    require(bool(initial_token), "SAM_BEE_INITIAL_LOGIN_FAILED")
    request("/api/auth/change-password", {
        "current_password": first_password,
        "new_password": password}, bearer=initial_token)
    require(bool(token(user, password)), "SAM_BEE_SHARED_LOGIN_NOT_VERIFIED")
    stamp_file(stamp, sha + "\n")
    print("SAM_BEE_OWNER=COMMISSIONED")


if __name__ == "__main__":
    try:
        p = argparse.ArgumentParser()
        p.add_argument("--credentials-file", type=Path, required=True)
        p.add_argument("--compose-file", type=Path, required=True)
        args = p.parse_args()
        reconcile(args.credentials_file, args.compose_file)
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError,
            subprocess.TimeoutExpired, urllib.error.URLError):
        print("SAM_BEE_OWNER=BLOCKED", file=sys.stderr)
        sys.exit(2)
