#!/usr/bin/env python3
"""Fail closed when a protected Media worker contains mixed Git releases."""
import argparse
import hashlib
import json
import os
import re
import stat
import sys
from pathlib import Path

WORKER_ROOT = Path("/opt/digitalhouses/bootstrap/pve-access")
MANIFEST = WORKER_ROOT / ".media-worker-release.json"
EXTERNAL = {
    "bootstrap/pve-access-audit-request.sh": Path("/usr/local/sbin/dh-pve-access-audit"),
    "bootstrap/dh-pve-media-release-verify.py": Path("/usr/local/sbin/dh-pve-media-release-verify"),
}


def require(condition, code):
    if not condition:
        raise RuntimeError("MEDIA_WORKER_RELEASE_" + code)


def checked_file(path):
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode) and not path.is_symlink(), "UNSAFE_FILE")
    require(info.st_uid == 0 and not (info.st_mode & 0o022), "UNSAFE_OWNER")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify(expected_commit, *, root=WORKER_ROOT, manifest=MANIFEST, external=None):
    require(bool(re.fullmatch(r"[0-9a-f]{40}", expected_commit)), "INVALID_COMMIT")
    external = EXTERNAL if external is None else external
    require(checked_file(manifest), "MISSING_MANIFEST")
    require(stat.S_IMODE(manifest.stat().st_mode) == 0o600, "MANIFEST_MODE")
    data = json.loads(manifest.read_text(encoding="utf-8"))
    require(isinstance(data, dict) and data.get("schema") == 1, "INVALID_MANIFEST")
    require(data.get("commit") == expected_commit, "COMMIT_MISMATCH")
    assets = data.get("assets")
    require(isinstance(assets, dict) and len(assets) >= 30, "INCOMPLETE_MANIFEST")
    require("roles/media_guest_setup/tasks/main.yml" in assets, "MISSING_MAIN")
    require("roles/media_guest_setup/tasks/apps.yml" in assets, "MISSING_APPS")
    require("roles/media_guest_setup/files/dh-sambee-owner.py" in assets, "MISSING_SAMBEE")
    require("playbooks/setup-pve-media-guest.yml" in assets, "MISSING_PLAYBOOK")
    for relative, digest in assets.items():
        require(isinstance(relative, str) and
                re.fullmatch(r"[a-zA-Z0-9_./-]+", relative) and
                ".." not in Path(relative).parts and
                not relative.startswith("/"), "INVALID_PATH")
        require(isinstance(digest, str) and bool(re.fullmatch(r"[a-f0-9]{64}", digest)),
                "INVALID_DIGEST")
        path = external.get(relative, root / relative)
        require(checked_file(path) == digest, "ASSET_MISMATCH")
    return len(assets)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--expected-commit", required=True)
    args = parser.parse_args()
    try:
        count = verify(args.expected_commit)
    except (RuntimeError, OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        print(str(exc) if str(exc).startswith("MEDIA_WORKER_RELEASE_") else
              "MEDIA_WORKER_RELEASE_INVALID", file=sys.stderr)
        return 2
    print("MEDIA_WORKER_RELEASE=PASS FILES=" + str(count))
    return 0


if __name__ == "__main__":
    sys.exit(main())
