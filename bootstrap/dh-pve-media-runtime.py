#!/usr/bin/env python3
"""Read only this site's Plex Agent MQTT secret from PVE root runtime."""
import argparse
import os
from pathlib import Path
import re
import stat
import sys
import yaml


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--site", required=True)
    site = p.parse_args().site
    if (os.geteuid() != 0 or
            not Path("/etc/pve/user.cfg").is_file() or
            re.fullmatch(r"[a-z][a-z0-9]+(?:-[a-z0-9]+)+", site) is None):
        raise ValueError("SITE_AUTH_INVALID")
    path = Path("/var/lib/ansible/runtime") / (site + ".yml")
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or
            info.st_gid != 0 or stat.S_IMODE(info.st_mode) != 0o600 or
            info.st_size > 131072):
        raise ValueError("SITE_RUNTIME_UNSAFE")
    with path.open("r", encoding="utf-8") as f:
        record = yaml.safe_load(f)
    runtime = record["runtime_secrets"]
    if runtime["schema_version"] != 1 or runtime["site_id"] != site:
        raise ValueError("SITE_RUNTIME_IDENTITY_DRIFT")
    mqtt = runtime["site_secrets"]["plex_agent"]["mqtt"]
    if not isinstance(mqtt, dict) or not all(
            isinstance(mqtt.get(k), str) and 0 < len(mqtt[k]) <= 512 and
            "\n" not in mqtt[k] and "\r" not in mqtt[k]
            for k in ("username", "password")):
        raise ValueError("SITE_MQTT_INVALID")
    data = {"runtime_secrets": {
        "schema_version": 1,
        "site_id": site,
        "site_secrets": {"plex_agent": {"mqtt": {
            "username": mqtt["username"], "password": mqtt["password"]}}}}}
    sys.stdout.write(yaml.safe_dump(data, sort_keys=False))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError, yaml.YAMLError):
        print("MEDIA_SITE_RUNTIME=BLOCKED", file=sys.stderr)
        sys.exit(2)
