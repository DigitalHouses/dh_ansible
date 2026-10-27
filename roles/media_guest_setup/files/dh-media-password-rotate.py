#!/usr/bin/env python3
"""Root-only media owner password rotation dispatcher; no HTTP entry point.

Old and new secrets are accepted ONLY from a bounded STDIN JSON record. No
password in argv, environment, Semaphore, Git, process status or application
logs. The customer web UI is deliberately not enabled by this CLI.
"""
import argparse
import importlib.machinery
import importlib.util
import ipaddress
import json
import os
from pathlib import Path
import re
import stat
import sys
import urllib.request

PROGS = Path("/usr/local/sbin")
MANIFEST = Path("/etc/digitalhouses/media-password-rotation.json")
REQUIRED = {"radarr", "sonarr", "prowlarr", "qbittorrent"}
OPTIONAL = {"jellyfin", "sambee", "filestash"}
KNOWN = REQUIRED | OPTIONAL


def require(ok, reason):
    if not ok:
        raise RuntimeError(reason)


def load(script):
    file = PROGS / script
    require(file.is_file() and not file.is_symlink(), "ROTATION_HELPER_MISSING")
    st = file.stat()
    require(st.st_uid == 0 and stat.S_IMODE(st.st_mode) == 0o700,
            "ROTATION_HELPER_UNSAFE")
    loader = importlib.machinery.SourceFileLoader(script.replace("-", "_"), str(file))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def configuration(path):
    require(path == MANIFEST, "ROTATION_MANIFEST_PATH_UNEXPECTED")
    require(path.is_file() and not path.is_symlink(),
            "ROTATION_MANIFEST_MISSING")
    st = path.stat()
    require(st.st_uid == 0 and stat.S_IMODE(st.st_mode) == 0o600
            and st.st_nlink == 1, "ROTATION_MANIFEST_UNSAFE")
    data = json.loads(path.read_text("ascii"))
    require(data.get("schema") == 1
            and isinstance(data.get("owner"), str)
            and re.fullmatch(r"[a-z][a-z0-9_]{2,30}", data["owner"])
            and isinstance(data.get("pool"), str)
            and re.fullmatch(r"[a-z][a-z0-9_-]{1,62}", data["pool"])
            and isinstance(data.get("apps"), dict)
            and set(data["apps"]) == KNOWN
            and all(isinstance(x, bool) for x in data["apps"].values())
            and all(data["apps"][a] for a in REQUIRED),
            "ROTATION_MANIFEST_INVALID")
    address = ipaddress.IPv4Address(data["guest_ipv4"])
    require(address.is_private and not address.is_loopback
            and not address.is_unspecified, "ROTATION_LAN_IP_UNSAFE")
    path = Path("/") / data["pool"] / "digitalhouses" / "credentials" / (
        data["owner"] + ".txt")
    return data, path


def build_adapters(data, owner_path, engine, adapters):
    username = data["owner"]
    credentials = owner_path.parent
    steps = {}
    if data["apps"]["jellyfin"]:
        steps["jellyfin"] = adapters.Jellyfin(username)
    if data["apps"]["sambee"]:
        steps["sambee"] = adapters.Sambee(username)
    for app in ("radarr", "sonarr", "prowlarr"):
        steps[app] = adapters.Servarr(app, username)
    steps["qbittorrent"] = adapters.QBittorrent(username, data["guest_ipv4"])
    steps["samba"] = adapters.Samba(username)
    if data["apps"]["filestash"]:
        steps["filestash"] = adapters.Filestash()
    # Protected digest stamps are changed only after all remote login changes.
    paths = [("samba_stamp", credentials / ".samba-sync" / (username + ".sha256")),
             ("qbittorrent_stamp", credentials / ".qbittorrent-admin.sha256")]
    if data["apps"]["sambee"]:
        paths.append(("sambee_stamp", credentials / ".sambee-owner.sha256"))
    for app in ("radarr", "sonarr", "prowlarr"):
        paths.append((app + "_stamp", credentials / (".arr-" + app + "-owner.sha256")))
    for name, file in paths:
        steps[name] = engine.StampAdapter(file, username)
    # The owner passport is the final commit step; never update it first.
    steps["passport"] = engine.PassportAdapter(owner_path)
    return steps


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=MANIFEST)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    require(os.geteuid() == 0, "ROTATION_ROOT_REQUIRED")
    # Never allow ambient HTTP_PROXY/HTTPS_PROXY to receive local API credentials.
    urllib.request.install_opener(
        urllib.request.build_opener(urllib.request.ProxyHandler({})))
    data, path = configuration(args.manifest)
    engine = load("dh-media-password-transaction")
    adapters = load("dh-media-password-adapters")
    steps = build_adapters(data, path, engine, adapters)
    if args.resume:
        old = new = None
    else:
        line = sys.stdin.buffer.readline(513)
        require(0 < len(line) <= 512 and line.endswith(b"\n"),
                "ROTATION_STDIN_INVALID")
        credentials = json.loads(line.decode("ascii"))
        require(isinstance(credentials, dict)
                and set(credentials) == {"old", "new"},
                "ROTATION_REQUEST_FIELDS_INVALID")
        old, new = credentials["old"], credentials["new"]
    return engine.execute(path, old, new, steps, resume=args.resume)


if __name__ == "__main__":
    try:
        result = main()
        print("MEDIA_OWNER_ROTATION=" + result)
        sys.exit(0 if result == "COMMITTED" else 2)
    except (OSError, ValueError, RuntimeError, KeyError, json.JSONDecodeError) as exc:
        # Codes only: passwords, raw API responses, tracebacks NEVER published.
        print("MEDIA_OWNER_ROTATION=BLOCKED_" + type(exc).__name__, file=sys.stderr)
        sys.exit(2)
