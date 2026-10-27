#!/usr/bin/env python3
"""Commission qBittorrent administrator using persistent DigitalHouses account."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request

URL = "http://127.0.0.1:8080"


def require(ok, reason):
    if not ok:
        raise RuntimeError(reason)


def load_credential(path):
    require(os.geteuid() == 0 and path.parent.name == "credentials" and
            path.parent.parent.name == "digitalhouses" and
            path.name.endswith(".txt") and
            path.name != "serzhan_admin.txt",
            "ACCOUNT_FILE_SCOPE_INVALID")
    st = path.lstat()
    require(stat.S_ISREG(st.st_mode) and st.st_uid == 0 and
            stat.S_IMODE(st.st_mode) == 0o640 and st.st_nlink == 1,
            "ACCOUNT_FILE_UNSAFE")
    lines = path.read_text("ascii").splitlines()
    username = path.stem
    require(len(lines) == 2 and lines[0] == "username=" + username and
            lines[1].startswith("password=") and
            6 <= len(lines[1][9:]) <= 8 and lines[1][9:].isalnum(),
            "ACCOUNT_FILE_INVALID")
    return username, lines[1][9:]


def call(path, payload=None):
    body = (urllib.parse.urlencode({"json": json.dumps(payload)}).encode("ascii")
            if payload is not None else None)
    request = urllib.request.Request(
        URL + path, data=body, headers={"Origin": URL,
                                       "Referer": URL,
                                       "Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            raw = response.read(256000)
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as err:
        raise RuntimeError("QBT_HTTP_" + str(err.code))


def atomic_stamp(path, content):
    fd, tmp = tempfile.mkstemp(prefix=".qbt-sync-", dir=path.parent)
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


def reconcile(path):
    username, password = load_credential(path)
    stamp = path.parent / ".qbittorrent-admin.sha256"
    desired_hash = hashlib.sha256((username + "\0" + password).encode()).hexdigest()
    if stamp.exists() or stamp.is_symlink():
        st = stamp.lstat()
        require(stat.S_ISREG(st.st_mode) and st.st_uid == 0 and
                stat.S_IMODE(st.st_mode) == 0o600 and
                st.st_nlink == 1, "QBT_STAMP_UNSAFE")
        require(stamp.read_text("ascii").strip() == desired_hash,
                "QBT_PASSWORD_ROTATION_NOT_AUTHORIZED")
        first = False
    else:
        first = True
    prefs = call("/api/v2/app/preferences")
    # On first commissioning a legacy qBittorrent may still whitelist LAN.
    # Correct it as part of the first credential write, then verify it is off.
    # On a managed rerun, a re-enabled LAN bypass is unexpected auth drift.
    require(prefs.get("bypass_local_auth") is True,
            "QBT_LOCAL_API_POLICY_INVALID")
    if not first:
        require(prefs.get("bypass_auth_subnet_whitelist_enabled") is False,
                "QBT_MANAGED_LAN_AUTH_BYPASS_DRIFT")
    if first:
        call("/api/v2/app/setPreferences", {
            "web_ui_username": username,
            "web_ui_password": password,
            "bypass_local_auth": True,
            "bypass_auth_subnet_whitelist_enabled": False,
            "bypass_auth_subnet_whitelist": "",
        })
    current = call("/api/v2/app/preferences")
    require(current.get("web_ui_username") == username and
            current.get("bypass_local_auth") is True and
            current.get("bypass_auth_subnet_whitelist_enabled") is False,
            "QBT_ADMIN_POLICY_NOT_VERIFIED")
    if first:
        atomic_stamp(stamp, desired_hash + "\n")
        print("QBT_ADMIN=COMMISSIONED")
    else:
        print("QBT_ADMIN=UNCHANGED")


if __name__ == "__main__":
    try:
        p = argparse.ArgumentParser()
        p.add_argument("--credentials-file", required=True, type=Path)
        reconcile(p.parse_args().credentials_file)
    except (OSError, ValueError, RuntimeError, urllib.error.URLError):
        print("QBT_ADMIN=BLOCKED", file=sys.stderr)
        sys.exit(2)
