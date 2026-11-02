#!/usr/bin/env python3
"""One-purpose, site-scoped PVE authorized_keys action invoked by Task 06.

Runs as root over verified, host-key-pinned Ansible SSH. Accepts ONLY a public
ED25519 key and expected node/IP/site. No passwords or private keys in argv.
"""
import argparse
import base64
import binascii
import ipaddress
import json
import os
from pathlib import Path
import re
import subprocess
import sys


class Blocked(Exception):
    pass


def require(ok, message):
    if not ok:
        raise Blocked(message)


def current_pve_identity():
    require(os.geteuid() == 0 and Path("/etc/pve/user.cfg").is_file(),
            "PVE_ROOT_REQUIRED")
    node = subprocess.check_output(["hostname", "-s"], text=True, timeout=8).strip()
    addresses = json.loads(subprocess.check_output(
        ["ip", "-j", "-4", "addr", "show", "dev", "vmbr0"],
        text=True, timeout=8,
    ))
    require(len(addresses) == 1, "PVE_BRIDGE_NOT_UNIQUE")
    live = [a["local"] for a in addresses[0].get("addr_info", [])
            if a.get("family") == "inet" and a.get("scope") == "global"]
    require(len(live) == 1, "PVE_IPV4_NOT_UNIQUE")
    return node, str(ipaddress.IPv4Address(live[0]))


def authorize(site, expected_node, expected_ip, key_text):
    node, ip = current_pve_identity()
    require(node == expected_node and ip == expected_ip,
            "PVE_HOST_IDENTITY_MISMATCH")
    require(re.fullmatch(r"[a-z][a-z0-9]*(-[a-z0-9]+)+", site),
            "INVALID_SITE_ID")
    parts = key_text.strip().split()
    require(len(parts) == 3 and parts[0] == "ssh-ed25519"
            and parts[2] == "dh-pve-" + site, "UNEXPECTED_PVE_PUBLIC_KEY")
    try:
        raw = base64.b64decode(parts[1], validate=True)
    except (ValueError, binascii.Error) as error:
        raise Blocked("INVALID_PVE_ED25519_KEY") from error
    require(b"ssh-ed25519" in raw[:20] and len(raw) >= 45,
            "INVALID_PVE_ED25519_KEY")
    folder = Path("/root/.ssh")
    require(not folder.is_symlink(), "PVE_ROOT_SSH_DIR_SYMLINK")
    folder.mkdir(mode=0o700, exist_ok=True)
    require(folder.is_dir() and folder.stat().st_uid == 0,
            "PVE_ROOT_SSH_DIR_INVALID")
    folder.chmod(0o700)
    dest = folder / "authorized_keys"
    if dest.is_symlink():
        require(dest.resolve(strict=True) == Path("/etc/pve/priv/authorized_keys"),
                "PVE_ROOT_AUTH_KEYS_UNEXPECTED_LINK")
    elif dest.exists():
        require(dest.is_file() and dest.stat().st_uid == 0,
                "PVE_ROOT_AUTH_KEYS_UNSAFE")
    prior = dest.read_text(encoding="utf-8") if dest.exists() else ""
    matches = sum(
        len(tokens := line.split()) >= 2
        and tokens[0] == "ssh-ed25519" and tokens[1] == parts[1]
        for line in prior.splitlines()
    )
    require(matches <= 1, "DUPLICATED_PVE_ROOT_SSH_KEY")
    if not matches:
        with dest.open("a", encoding="utf-8") as out:
            if prior and not prior.endswith("\n"):
                out.write("\n")
            out.write(" ".join(parts) + "\n")
        if not dest.is_symlink():
            dest.chmod(0o600)
    return "UNCHANGED" if matches else "AUTHORIZED"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--site", required=True)
    parser.add_argument("--node", required=True)
    parser.add_argument("--ip", required=True)
    parser.add_argument("--key-b64", required=True)
    args = parser.parse_args()
    key = base64.b64decode(args.key_b64, validate=True).decode("ascii")
    result = authorize(args.site, args.node, args.ip, key)
    print("PVE_NATIVE_SSH_KEY=" + result)


if __name__ == "__main__":
    try:
        main()
    except (Blocked, OSError, ValueError, UnicodeDecodeError, binascii.Error,
            subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        print("PVE_NATIVE_SSH_KEY=BLOCKED:" + type(error).__name__, file=sys.stderr)
        sys.exit(2)
