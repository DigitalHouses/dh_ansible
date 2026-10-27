#!/usr/bin/env python3
"""Reconcile only Filestash Samba login, preserving all unrelated state."""
import json
import os
from pathlib import Path
import pwd
import sys
import tempfile

PATH = Path("/opt/dh-filestash/data/state/config/config.json")


def desired():
    label = "DigitalHouses SMB"
    return {
        "connections": [{"label": label, "type": "samba"}],
        "middleware": {
            "identity_provider": {
                "type": "passthrough",
                "params": json.dumps({"strategy": "username_and_password"})},
            "attribute_mapping": {
                "related_backend": label,
                "params": json.dumps({
                    label: {"type": "samba", "host": "127.0.0.1",
                            "username": "{{ .user }}",
                            "password": "{{ .password }}"}})}
        }
    }


def reconcile(path=PATH):
    if path.is_symlink() or not path.parent.is_dir():
        raise RuntimeError("STATE_PATH_UNSAFE")
    data = json.loads(path.read_text("utf-8")) if path.exists() else {}
    if not isinstance(data, dict):
        raise RuntimeError("STATE_INVALID")
    for key, value in desired().items():
        current = data.get(key)
        if current not in (None, [], {}, value):
            raise RuntimeError("EXISTING_" + key.upper() + "_CONFLICT")
        data[key] = value
    content = (json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode()
    if path.exists() and path.read_bytes() == content:
        print("FILESTASH_SMB=UNCHANGED")
        return
    user = pwd.getpwnam("filestash")
    fd, stage = tempfile.mkstemp(prefix=".dh-smb-", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        os.fchown(fd, user.pw_uid, user.pw_gid)
        with os.fdopen(fd, "wb") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(stage, path)
    finally:
        if os.path.exists(stage):
            os.unlink(stage)
    print("FILESTASH_SMB=RECONCILED")


if __name__ == "__main__":
    try:
        reconcile()
    except (OSError, ValueError, RuntimeError) as error:
        print("FILESTASH_SMB=BLOCKED_" + type(error).__name__, file=sys.stderr)
        sys.exit(2)
