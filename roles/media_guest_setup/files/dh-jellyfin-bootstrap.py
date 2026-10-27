#!/usr/bin/env python3
"""Complete an unclaimed Jellyfin startup wizard using a protected local passport."""
import json
import os
from pathlib import Path
import stat
import sys
import urllib.request
import urllib.error

SECRET = Path("/etc/digitalhouses/jellyfin-admin.json")
BASE = "http://127.0.0.1:8096"


def require(ok, msg):
    if not ok:
        raise RuntimeError(msg)


def request(method, path, payload=None):
    body = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        BASE + path, data=body, method=method,
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=12) as response:
            data = response.read(1024 * 1024)
            return json.loads(data) if data else {}
    except urllib.error.HTTPError as error:
        raise RuntimeError("JELLYFIN_HTTP_" + str(error.code))


def bootstrap():
    require(os.geteuid() == 0, "ROOT_ONLY")
    require(not SECRET.is_symlink() and SECRET.is_file(), "ADMIN_PASSPORT_MISSING")
    st = SECRET.stat()
    require(st.st_uid == 0 and stat.S_IMODE(st.st_mode) == 0o600,
            "ADMIN_PASSPORT_UNSAFE")
    credentials = json.loads(SECRET.read_text("utf-8"))
    require(isinstance(credentials, dict) and
            credentials.get("username") == "serzhan_admin" and
            isinstance(credentials.get("password"), str) and
            len(credentials["password"]) >= 16, "ADMIN_PASSPORT_INVALID")
    public = request("GET", "/System/Info/Public")
    require(isinstance(public, dict), "JELLYFIN_PUBLIC_STATUS_INVALID")
    if not public.get("StartupWizardCompleted"):
        request("POST", "/Startup/User",
                {"Name": "serzhan_admin", "Password": credentials["password"]})
        request("POST", "/Startup/RemoteAccess",
                {"EnableRemoteAccess": False, "EnableAutomaticPortMapping": False})
        request("POST", "/Startup/Complete")
        require(request("GET", "/System/Info/Public").get("StartupWizardCompleted") is True,
                "JELLYFIN_STARTUP_NOT_CONFIRMED")
        print("JELLYFIN_WIZARD=COMPLETED")
    else:
        print("JELLYFIN_WIZARD=ALREADY_COMPLETED")
    auth = request("POST", "/Users/AuthenticateByName",
                   {"Username": "serzhan_admin", "Pw": credentials["password"]})
    require(isinstance(auth, dict) and bool(auth.get("AccessToken")),
            "JELLYFIN_MANAGED_LOGIN_FAILED")
    print("JELLYFIN_ADMIN_AUTH=PASS")


if __name__ == "__main__":
    try:
        bootstrap()
    except (OSError, ValueError, RuntimeError, urllib.error.URLError) as e:
        print("JELLYFIN_SETUP=BLOCKED_" + type(e).__name__, file=sys.stderr)
        sys.exit(2)
