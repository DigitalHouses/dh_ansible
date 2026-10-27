#!/usr/bin/env python3
"""Complete an unclaimed Jellyfin startup wizard using a protected local passport."""
import argparse
import json
import os
from pathlib import Path
import stat
import urllib.parse
import sys
import re
import urllib.request
import urllib.error

BASE = "http://127.0.0.1:8096"
DIAGNOSTIC = Path("/run/dh-jellyfin-bootstrap.status")
STAGE = ["NOT_STARTED"]


def stage_name(method, path):
    route = path.split("?", 1)[0]
    names = {
        "/System/Info/Public": "PUBLIC_STATUS",
        "/Startup/Configuration": "STARTUP_CONFIG",
        "/Startup/User": "STARTUP_USER",
        "/Startup/RemoteAccess": "STARTUP_REMOTE_ACCESS",
        "/Startup/Complete": "STARTUP_COMPLETE",
        "/Users/AuthenticateByName": "OWNER_AUTH",
        "/Library/VirtualFolders": "LIBRARY",
    }
    return method + "_" + names.get(route, "OTHER_FIXED_ENDPOINT")


def sanitized_diagnostic(error):
    stage = STAGE[0]
    code = str(error) if isinstance(error, RuntimeError) else type(error).__name__
    if not re.fullmatch(r"[A-Z][A-Z0-9_]{2,75}", code):
        code = "UNCLASSIFIED"
    if not re.fullmatch(r"[A-Z][A-Z0-9_]{2,75}", stage):
        stage = "UNKNOWN_STAGE"
    return "JELLYFIN_STAGE=" + stage + " JELLYFIN_ERROR=" + code + "\n"


def record_diagnostic(error):
    if os.geteuid() != 0:
        return
    if DIAGNOSTIC.exists() or DIAGNOSTIC.is_symlink():
        st = DIAGNOSTIC.lstat()
        if (not stat.S_ISREG(st.st_mode) or st.st_uid != 0 or
                stat.S_IMODE(st.st_mode) != 0o600 or st.st_nlink != 1):
            return
    fd = os.open(DIAGNOSTIC,
                 os.O_WRONLY | os.O_CREAT | os.O_TRUNC |
                 getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="ascii") as log:
            log.write(sanitized_diagnostic(error))
            log.flush()
            os.fsync(log.fileno())
    finally:
        pass


def clear_diagnostic():
    if DIAGNOSTIC.exists() and not DIAGNOSTIC.is_symlink():
        st = DIAGNOSTIC.lstat()
        if stat.S_ISREG(st.st_mode) and st.st_uid == 0 and stat.S_IMODE(st.st_mode) == 0o600:
            DIAGNOSTIC.unlink()




def require(ok, msg):
    if not ok:
        raise RuntimeError(msg)


def request(method, path, payload=None, token=None):
    body = json.dumps(payload).encode() if payload is not None else None
    headers = {"Content-Type": "application/json",
               "Authorization": 'MediaBrowser Client="DigitalHouses", Device="MediaServer", DeviceId="dh-mediaserver", Version="1.0"'}
    if token:
        headers["X-Emby-Token"] = token
    STAGE[0] = stage_name(method, path)
    req = urllib.request.Request(BASE + path, data=body,
                                 method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=12) as response:
            data = response.read(1024 * 1024)
            return json.loads(data) if data else {}
    except urllib.error.HTTPError as error:
        raise RuntimeError("JELLYFIN_HTTP_" + str(error.code))


def reconcile_libraries(media_root, token):
    require(media_root.is_absolute() and media_root.name == "media" and
            media_root.parent.name.endswith("-pool") and
            media_root.is_dir() and not media_root.is_symlink(),
            "JELLYFIN_MEDIA_ROOT_UNSAFE")
    folders = request("GET", "/Library/VirtualFolders", token=token)
    require(isinstance(folders, list), "JELLYFIN_LIBRARY_LIST_INVALID")
    desired = (("Movies", "movies", "movies"),
               ("TV Shows", "tvshows", "tvshows"),
               ("Music", "music", "music"))
    for name, kind, subdir in desired:
        destination = media_root / subdir
        require(destination.is_dir() and not destination.is_symlink(),
                "JELLYFIN_MEDIA_PATH_MISSING")
        matching = [x for x in folders if x.get("Name") == name]
        require(len(matching) <= 1, "JELLYFIN_LIBRARY_DUPLICATE")
        if matching:
            locations = matching[0].get("Locations") or []
            require(locations == [str(destination)] and
                    matching[0].get("CollectionType") == kind,
                    "JELLYFIN_LIBRARY_DRIFT")
            print("JELLYFIN_LIBRARY_" + kind.upper() + "=UNCHANGED")
            continue
        require(not any(str(destination) in (x.get("Locations") or [])
                        for x in folders), "JELLYFIN_LIBRARY_PATH_CONFLICT")
        params = urllib.parse.urlencode({
            "name": name, "collectionType": kind, "refreshLibrary": "false"})
        request("POST", "/Library/VirtualFolders?" + params,
                {"LibraryOptions": {"PathInfos": [
                    {"Path": str(destination)}]}}, token=token)
        refreshed = request("GET", "/Library/VirtualFolders", token=token)
        require(any(x.get("Name") == name and
                    str(destination) in (x.get("Locations") or [])
                    for x in refreshed), "JELLYFIN_LIBRARY_CREATE_UNCONFIRMED")
        folders = refreshed
        print("JELLYFIN_LIBRARY_" + kind.upper() + "=CREATED")
    print("JELLYFIN_LIBRARIES=READY")


def bootstrap(secret_file, media_root):
    require(os.geteuid() == 0, "ROOT_ONLY")
    require(secret_file.parent.name == 'credentials' and
            secret_file.parent.parent.name == 'digitalhouses' and
            secret_file.name.endswith('.txt') and
            secret_file.stem != 'serzhan_admin', 'PASSPORT_LOCATION_INVALID')
    require(not secret_file.is_symlink() and secret_file.is_file(), "ADMIN_PASSPORT_MISSING")
    st = secret_file.stat()
    require(st.st_uid == 0 and stat.S_IMODE(st.st_mode) == 0o640,
            "ADMIN_PASSPORT_UNSAFE")
    lines = secret_file.read_text("ascii").splitlines()
    username = secret_file.stem
    require(len(lines) == 2 and lines[0] == "username=" + username and
            lines[1].startswith("password=") and
            6 <= len(lines[1][9:]) <= 8 and lines[1][9:].isalnum(),
            "ADMIN_PASSPORT_INVALID")
    credentials = {"username": username, "password": lines[1][9:]}
    public = request("GET", "/System/Info/Public")
    require(isinstance(public, dict), "JELLYFIN_PUBLIC_STATUS_INVALID")
    if not public.get("StartupWizardCompleted"):
        initial = request("GET", "/Startup/Configuration")
        require(isinstance(initial, dict), "JELLYFIN_STARTUP_CONFIG_INVALID")
        request("POST", "/Startup/Configuration", initial)
        request("POST", "/Startup/User",
                {"Name": credentials["username"], "Password": credentials["password"]})
        request("POST", "/Startup/RemoteAccess",
                {"EnableRemoteAccess": False, "EnableAutomaticPortMapping": False})
        request("POST", "/Startup/Complete")
        require(request("GET", "/System/Info/Public").get("StartupWizardCompleted") is True,
                "JELLYFIN_STARTUP_NOT_CONFIRMED")
        print("JELLYFIN_WIZARD=COMPLETED")
    else:
        print("JELLYFIN_WIZARD=ALREADY_COMPLETED")
    auth = request("POST", "/Users/AuthenticateByName",
                   {"Username": credentials["username"], "Pw": credentials["password"]})
    require(isinstance(auth, dict) and bool(auth.get("AccessToken")),
            "JELLYFIN_MANAGED_LOGIN_FAILED")
    print("JELLYFIN_ADMIN_AUTH=PASS")
    reconcile_libraries(media_root, auth['AccessToken'])
    clear_diagnostic()


if __name__ == "__main__":
    try:
        parser = argparse.ArgumentParser()
        parser.add_argument('--credential-file', type=Path, required=True)
        parser.add_argument('--media-root', type=Path, required=True)
        args = parser.parse_args()
        bootstrap(args.credential_file, args.media_root)
    except (OSError, ValueError, RuntimeError, urllib.error.URLError) as e:
        record_diagnostic(e)
        print("JELLYFIN_SETUP=BLOCKED", file=sys.stderr)
        sys.exit(2)
