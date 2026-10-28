#!/usr/bin/env python3
"""Configure one DigitalHouses native Plex/Arr media guest.

No external tokens. Reads already-generated application API keys from the
guest's own XML config, never prints/stores them elsewhere. Only manages
DigitalHouses's explicit connections, root folders and qBittorrent categories.
Never enables downloads, adds trackers/indexers, or formats ZFS.
Moves only empty qBittorrent staging paths to the preapproved same-dataset root.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
from pathlib import Path
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET


class Blocked(Exception):
    pass


ARR = {
    "radarr": (7878, "v3"),
    "sonarr": (8989, "v3"),
    "prowlarr": (9696, "v1"),
}
BASE = "http://127.0.0.1:"
QBT = BASE + "8080"
MANAGED_DOWNLOAD_CLIENT = "DigitalHouses qBittorrent"
MANAGED_APPLICATIONS = {
    "radarr": "DigitalHouses Radarr",
    "sonarr": "DigitalHouses Sonarr",
}


def require(cond: bool, explanation: str):
    if not cond:
        raise Blocked(explanation)


def request(url: str, *, method="GET", data=None, key=None, form=False):
    headers = {"Accept": "application/json"}
    if key:
        headers["X-Api-Key"] = key
    body = None
    if data is not None:
        if form:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
            body = urllib.parse.urlencode(data).encode()
        else:
            headers["Content-Type"] = "application/json"
            body = json.dumps(data, separators=(",", ":")).encode()
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=25) as reply:
            raw = reply.read(1024 * 1024)
            return json.loads(raw) if raw and "json" in reply.headers.get("Content-Type", "") else (
                json.loads(raw) if raw and raw[:1] in (b"{", b"[") else None
            )
    except urllib.error.HTTPError as exc:
        # API errors may echo keys or qBt credential details: NEVER print body or URL.
        raise Blocked("UPSTREAM_HTTP_" + str(exc.code)) from None
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        raise Blocked("UPSTREAM_" + type(exc).__name__) from None


def api(name: str, path: str, *, method="GET", data=None):
    port, version = ARR[name]
    return request(f"{BASE}{port}/api/{version}/{path}", method=method, data=data,
                   key=api_key(name))


def api_key(name: str) -> str:
    cfg = Path("/var/lib") / name / "config.xml"
    require(cfg.is_file() and not cfg.is_symlink(), name.upper() + "_CONFIG_MISSING")
    root = ET.parse(cfg).getroot()
    key = root.findtext("ApiKey", "")
    require(len(key) >= 24 and key.isalnum(), name.upper() + "_API_KEY_UNAVAILABLE")
    return key


def field(schema: dict, name: str, value):
    matches = [x for x in schema.get("fields", []) if x.get("name") == name]
    require(len(matches) == 1, "SCHEMA_FIELD_MISSING_" + name)
    matches[0]["value"] = value


def qbt(path: str, *, method="GET", data=None):
    return request(QBT + "/api/v2/" + path, method=method, data=data, form=data is not None)


def safe_empty_previous_staging():
    old = Path("/srv/downloads")
    require(old.is_dir() and not old.is_symlink(),
            "QBT_PREVIOUS_STAGING_UNSAFE")
    require(not any(entry.is_file() or entry.is_symlink()
                    for entry in old.rglob("*")),
            "QBT_PREVIOUS_DOWNLOADS_NOT_EMPTY")
    torrents = qbt("torrents/info")
    require(isinstance(torrents, list) and not torrents,
            "QBT_DOWNLOAD_MIGRATION_TORRENTS_PRESENT")


def ensure_qbt_access(download_root: str):
    p = qbt("app/preferences")
    require(isinstance(p, dict), "QBT_AUTH_NOT_CONFIGURED")
    require(bool(p.get("bypass_local_auth")), "QBT_LOOPBACK_API_NOT_ALLOWED")
    current = str(p.get("save_path", "")).rstrip("/")
    temp = str(p.get("temp_path", "")).rstrip("/")
    require(p.get("temp_path_enabled") is True,
            "QBT_TEMP_PATH_NOT_ENABLED")
    if current != download_root:
        require(download_root != "/srv/downloads"
                and current == "/srv/downloads"
                and temp == "/srv/downloads/incomplete",
                "QBT_DOWNLOAD_PATH_UNEXPECTED")
        safe_empty_previous_staging()
        categories = qbt("torrents/categories")
        require(isinstance(categories, dict), "QBT_CATEGORIES_INVALID")
        for name, relative in (("radarr", "complete/movies"),
                               ("tv-sonarr", "complete/series"),
                               ("prowlarr", "complete/prowlarr")):
            require(name in categories and
                    categories[name].get("savePath", "").rstrip("/") ==
                    "/srv/downloads/" + relative,
                    "QBT_CATEGORY_MIGRATION_DRIFT_" + name.upper().replace("-", "_"))
        qbt("app/setPreferences", method="POST", data={
            "json": json.dumps({"save_path": download_root,
                                "temp_path": download_root + "/incomplete",
                                "temp_path_enabled": True})})
        p = qbt("app/preferences")
        require(isinstance(p, dict), "QBT_POST_MIGRATION_PREFS_INVALID")
        print("QBT_DOWNLOAD_ROOT=MIGRATED", flush=True)
    require(str(p.get("save_path", "")).rstrip("/") == download_root
            and str(p.get("temp_path", "")).rstrip("/") == download_root + "/incomplete",
            "QBT_DOWNLOAD_PATH_UNEXPECTED")
    print("QBT_LOCAL_API=PASS", flush=True)


def ensure_categories(download_root: str):
    categories = qbt("torrents/categories")
    require(isinstance(categories, dict), "QBT_CATEGORIES_INVALID")
    for name, relative in (("radarr", "complete/movies"),
                           ("tv-sonarr", "complete/series"),
                           ("prowlarr", "complete/prowlarr")):
        target = download_root + "/" + relative
        require(Path(target).is_dir(), "QBT_CATEGORY_FOLDER_MISSING_" + name)
        if name not in categories:
            qbt("torrents/createCategory", method="POST", data={"category": name, "savePath": target})
            print("QBT_CATEGORY_" + name.upper().replace("-", "_") + "=CREATED", flush=True)
        else:
            current = categories[name].get("savePath", "").rstrip("/")
            if current != target:
                require(download_root != "/srv/downloads"
                        and current == "/srv/downloads/" + relative,
                        "QBT_CATEGORY_DRIFT_" + name)
                safe_empty_previous_staging()
                qbt("torrents/editCategory", method="POST",
                    data={"category": name, "savePath": target})
                print("QBT_CATEGORY_" + name.upper().replace("-", "_") + "=MIGRATED", flush=True)
            else:
                print("QBT_CATEGORY_" + name.upper().replace("-", "_") + "=UNCHANGED", flush=True)
    current = qbt("torrents/categories")
    require(all(k in current for k in ("radarr", "tv-sonarr", "prowlarr")),
            "QBT_CATEGORY_VERIFICATION_FAILED")


def ensure_client(app: str):
    existing = api(app, "downloadclient")
    require(isinstance(existing, list), "DOWNLOAD_CLIENT_LIST_INVALID_" + app)
    entries = [x for x in existing if x.get("name") == MANAGED_DOWNLOAD_CLIENT]
    require(len(entries) <= 1, "DOWNLOAD_CLIENT_DUPLICATE_" + app)
    if entries:
        require(entries[0].get("implementation") == "QBittorrent",
                "DOWNLOAD_CLIENT_UNEXPECTED_" + app)
        print("DOWNLOAD_CLIENT_" + app.upper() + "=UNCHANGED", flush=True)
        return
    schemas = api(app, "downloadclient/schema")
    candidates = [x for x in schemas if x.get("implementation") == "QBittorrent"]
    require(len(candidates) == 1, "QBT_SCHEMA_MISSING_" + app)
    item = copy.deepcopy(candidates[0])
    item.update(name=MANAGED_DOWNLOAD_CLIENT, enable=True, priority=1, tags=[])
    field(item, "host", "127.0.0.1")
    field(item, "port", 8080)
    field(item, "useSsl", False)
    field(item, "username", "")
    field(item, "password", "")
    if app == "radarr":
        field(item, "movieCategory", "radarr")
    elif app == "sonarr":
        field(item, "tvCategory", "tv-sonarr")
    else:
        field(item, "category", "prowlarr")
    # Required provider test: catches missing credentials/port/category.
    tests = api(app, "downloadclient/test", method="POST", data=item)
    require(tests is None or tests == [] or tests == {} or tests == "{}" or tests == "[]" or tests == "", "DOWNLOAD_CLIENT_TEST_FAILED_" + app)
    api(app, "downloadclient", method="POST", data=item)
    print("DOWNLOAD_CLIENT_" + app.upper() + "=CREATED", flush=True)


def ensure_root(app: str, path: str):
    existing = api(app, "rootfolder")
    require(isinstance(existing, list), "ROOT_FOLDER_LIST_INVALID_" + app)
    if any(x.get("path") == path for x in existing):
        print("ROOT_FOLDER_" + app.upper() + "=UNCHANGED", flush=True)
        return
    require(Path(path).is_dir(), "ROOT_FOLDER_MISSING_" + app)
    api(app, "rootfolder", method="POST", data={"path": path})
    require(any(x.get("path") == path for x in api(app, "rootfolder")),
            "ROOT_FOLDER_CREATE_FAILED_" + app)
    print("ROOT_FOLDER_" + app.upper() + "=CREATED", flush=True)


def ensure_application(app: str):
    existing = api("prowlarr", "applications")
    require(isinstance(existing, list), "PROWLARR_APP_LIST_INVALID")
    entries = [x for x in existing if x.get("name") == MANAGED_APPLICATIONS[app]]
    require(len(entries) <= 1, "PROWLARR_APP_DUPLICATED_" + app)
    if entries:
        require(entries[0].get("implementation", "").lower() == app,
                "PROWLARR_APP_IMPLEMENTATION_DRIFT_" + app)
        print("PROWLARR_APP_" + app.upper() + "=UNCHANGED", flush=True)
        return
    schemas = api("prowlarr", "applications/schema")
    candidates = [x for x in schemas if x.get("implementation", "").lower() == app]
    require(len(candidates) == 1, "PROWLARR_APP_SCHEMA_MISSING_" + app)
    item = copy.deepcopy(candidates[0])
    item.update(name=MANAGED_APPLICATIONS[app], enable=True, syncLevel="fullSync", tags=[])
    field(item, "prowlarrUrl", BASE + "9696")
    field(item, "baseUrl", BASE + str(ARR[app][0]))
    field(item, "apiKey", api_key(app))
    tests = api("prowlarr", "applications/test", method="POST", data=item)
    require(tests is None or tests == [] or tests == {} or tests == "{}" or tests == "[]" or tests == "", "PROWLARR_APP_TEST_FAILED_" + app)
    api("prowlarr", "applications", method="POST", data=item)
    print("PROWLARR_APP_" + app.upper() + "=CREATED", flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--downloads", required=True)
    parser.add_argument("--library-root", required=True)
    args = parser.parse_args()
    root = Path(args.downloads)
    library = Path(args.library_root)
    require(library.is_dir() and not library.is_symlink(), "ZFS_MEDIA_LIBRARY_MISSING")
    require(str(root) in ("/srv/downloads", str(library / "downloads")),
            "NONCANONICAL_DOWNLOAD_ROOT")
    require(root.is_dir() and not root.is_symlink(), "DOWNLOAD_ROOT_MISSING")
    if str(root) != "/srv/downloads":
        require(root.parent == library
                and os.stat(root).st_dev == os.stat(library).st_dev,
                "DOWNLOAD_ROOT_NOT_SAME_FILESYSTEM")
    require(os.geteuid() == 0, "ROOT_GUEST_ONLY")
    # Manage only OS-system-disk staging; protected ZFS media is not changed.
    for path in ("complete/movies", "complete/series", "complete/prowlarr"):
        require((root / path).is_dir(), "STAGING_FOLDER_MISSING_" + path.replace("/", "_"))
    ensure_qbt_access(str(root))
    ensure_categories(str(root))
    for app, folder in (("radarr", "movies"), ("sonarr", "tvshows")):
        ensure_root(app, str(library / folder))
    for app in ("radarr", "sonarr", "prowlarr"):
        ensure_client(app)
    for app in ("radarr", "sonarr"):
        ensure_application(app)
    for app in ("radarr", "sonarr"):
        records = api("prowlarr", "applications")
        require(any(r.get("name") == MANAGED_APPLICATIONS[app] for r in records),
                "PROWLARR_INTEGRATION_NOT_VERIFIED_" + app)
    print("MEDIA_APPLICATIONS_WIRING=PASS", flush=True)
    print("EXTERNAL_INDEXERS=OWNER_SELECTION_REQUIRED", flush=True)
    print("ZFS_LIBRARY=UNTOUCHED", flush=True)


if __name__ == "__main__":
    try:
        main()
    except (Blocked, OSError, KeyError, TypeError, ET.ParseError) as error:
        reason = str(error) if isinstance(error, Blocked) else type(error).__name__
        print("MEDIA_APPLICATIONS_WIRING=BLOCKED_" + reason, file=sys.stderr, flush=True)
        sys.exit(2)
