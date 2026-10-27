#!/usr/bin/env python3
"""Bootstrap native Servarr Forms login with the existing SMB owner identity.

Only an unclaimed local Forms setup is created. Existing configured identities
are never replaced by ordinary Ansible apply. API keys remain local/unchanged.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

APPS = {"radarr": (7878, "v3"), "sonarr": (8989, "v3"),
        "prowlarr": (9696, "v1")}
NAME = re.compile(r"^[a-z][a-z0-9_]{2,30}$")


def require(value, reason):
    if not value:
        raise RuntimeError(reason)


def credential(path):
    require(os.geteuid() == 0 and path.parent.name == "credentials" and
            path.parent.parent.name == "digitalhouses" and
            NAME.fullmatch(path.stem) is not None and
            path.stem != "serzhan_admin", "ARR_OWNER_SCOPE_INVALID")
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and
            stat.S_IMODE(info.st_mode) == 0o640 and
            info.st_nlink == 1, "ARR_OWNER_FILE_UNSAFE")
    lines = path.read_text("ascii").splitlines()
    require(len(lines) == 2 and lines[0] == "username=" + path.stem and
            lines[1].startswith("password=") and
            6 <= len(lines[1][9:]) <= 8 and lines[1][9:].isalnum(),
            "ARR_OWNER_CREDENTIAL_INVALID")
    return path.stem, lines[1][9:]


def api_key(app):
    path = Path("/var/lib") / app / "config.xml"
    require(path.is_file() and not path.is_symlink(), "ARR_CONFIG_MISSING")
    api = ET.parse(path).getroot().findtext("ApiKey", "")
    require(api.isalnum() and len(api) >= 24, "ARR_API_KEY_MISSING")
    return api


def request(app, key, method="GET", values=None, rid=None):
    port, version = APPS[app]
    url = "http://127.0.0.1:%s/api/%s/config/host" % (port, version)
    if rid is not None:
        url += "/" + str(rid)
    body = json.dumps(values).encode("utf-8") if values is not None else None
    req = urllib.request.Request(
        url, method=method, data=body,
        headers={"X-Api-Key": key, "Content-Type": "application/json",
                 "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=25) as response:
            return json.loads(response.read(131072))
    except urllib.error.HTTPError as exc:
        # HTTP error body can include secrets; never print.
        raise RuntimeError("ARR_HTTP_" + str(exc.code)) from None


def stamp(path, digest):
    fd, tmp = tempfile.mkstemp(prefix=".arr-owner-", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="ascii") as file:
            file.write(digest + "\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def configure_one(app, credential_path, username, password):
    token = api_key(app)
    state_file = credential_path.parent / (".arr-" + app + "-owner.sha256")
    wanted = hashlib.sha256((username + "\0" + password).encode()).hexdigest()
    if state_file.exists() or state_file.is_symlink():
        info = state_file.lstat()
        require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and
                stat.S_IMODE(info.st_mode) == 0o600 and
                info.st_nlink == 1, "ARR_OWNER_STAMP_UNSAFE")
        require(state_file.read_text("ascii").strip() == wanted,
                "ARR_OWNER_ROTATION_NOT_COMMISSIONED")
    config = request(app, token)
    require(isinstance(config, dict) and isinstance(config.get("id"), int),
            "ARR_HOST_CONFIG_INVALID")
    method = str(config.get("authenticationMethod", "")).lower()
    required = str(config.get("authenticationRequired", "")).lower()
    if state_file.exists():
        require(method == "forms" and required == "enabled" and
                config.get("username") == username,
                "ARR_MANAGED_LOGIN_DRIFT")
        print("ARR_AUTH_" + app.upper() + "=UNCHANGED")
        return
    require(method in ("none", "0", ""), "ARR_PREEXISTING_LOGIN_UNMANAGED")
    desired = dict(config)
    desired.update(authenticationMethod="forms",
                   authenticationRequired="enabled",
                   username=username, password=password,
                   passwordConfirmation=password)
    # This is the official host-config API; it hashes the password internally.
    request(app, token, method="PUT", rid=config["id"], values=desired)
    actual = request(app, token)
    require(str(actual.get("authenticationMethod", "")).lower() == "forms" and
            str(actual.get("authenticationRequired", "")).lower() == "enabled" and
            actual.get("username") == username, "ARR_MANAGED_LOGIN_NOT_VERIFIED")
    stamp(state_file, wanted)
    print("ARR_AUTH_" + app.upper() + "=COMMISSIONED")


def main(path):
    owner, password = credential(path)
    for app in APPS:
        configure_one(app, path, owner, password)
    print("ARR_OWNER_AUTH=READY")


if __name__ == "__main__":
    try:
        p = argparse.ArgumentParser()
        p.add_argument("--credentials-file", type=Path, required=True)
        args = p.parse_args()
        main(args.credentials_file)
    except (OSError, RuntimeError, ValueError, KeyError,
            urllib.error.URLError, ET.ParseError):
        print("ARR_OWNER_AUTH=BLOCKED", file=sys.stderr)
        sys.exit(2)
