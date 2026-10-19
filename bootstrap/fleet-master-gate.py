#!/usr/bin/env python3
"""Read-only Ansible Vault master-password authentication gate for PVE bootstrap.

Validates an independently SHA-256-pinned AES256 Vault ciphertext by its HMAC
using Python stdlib only; does not decrypt, change files, install packages,
start guests, or transmit the password. Full Ansible Vault content validation
is a separate post-runtime check before infrastructure apply.
"""
from __future__ import annotations

import argparse
import getpass
import hashlib
import hmac
import os
from pathlib import Path
import re
import stat
import sys

MAX_VAULT_BYTES = 4 * 1024 * 1024
ALLOWED_HEADERS = {b"$ANSIBLE_VAULT;1.1;AES256", b"$ANSIBLE_VAULT;1.2;AES256"}


class Blocked(ValueError):
    pass


def split_ciphertext(data: bytes) -> tuple[bytes, bytes, bytes]:
    lines = data.splitlines()
    if len(lines) < 2 or lines[0] not in ALLOWED_HEADERS:
        raise Blocked("Unsupported Vault envelope (whole-file AES256 required)")
    armored = b"".join(lines[1:])
    if not armored or not re.fullmatch(rb"[0-9a-fA-F]+", armored) or len(armored) % 2:
        raise Blocked("Malformed Vault armor")
    try:
        salt_hex, mac_hex, cipher_hex = bytes.fromhex(armored.decode("ascii")).split(b"\n")
        if (len(salt_hex) != 64 or len(mac_hex) != 64 or not cipher_hex
                or len(cipher_hex) % 32 or
                any(not re.fullmatch(rb"[0-9a-fA-F]+", value)
                    for value in (salt_hex, mac_hex, cipher_hex))):
            raise ValueError()
        salt = bytes.fromhex(salt_hex.decode("ascii"))
        mac = bytes.fromhex(mac_hex.decode("ascii"))
        ciphertext = bytes.fromhex(cipher_hex.decode("ascii"))
    except ValueError:
        raise Blocked("Malformed Vault AES256 payload") from None
    if len(salt) != 32 or len(mac) != 32:
        raise Blocked("Unexpected Vault AES256 parameters")
    return salt, mac, ciphertext


def password_authenticates(vault: bytes, password: bytes) -> bool:
    """Ansible Vault AES256 v1.1/1.2: PBKDF2(10000, 80 bytes), HMAC key [32:64]."""
    salt, expected_mac, ciphertext = split_ciphertext(vault)
    derived = hashlib.pbkdf2_hmac("sha256", password, salt, 10000, 80)
    computed_mac = hmac.new(derived[32:64], ciphertext, hashlib.sha256).digest()
    return hmac.compare_digest(expected_mac, computed_mac)


def read_pinned_vault(path: Path, sha256: str) -> bytes:
    if not re.fullmatch(r"[0-9a-fA-F]{64}", sha256):
        raise Blocked("Trusted Vault SHA-256 is required")
    if path.is_symlink() or not path.is_file():
        raise Blocked("Vault file missing or symlinked")
    info = path.stat()
    if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o022:
        raise Blocked("Vault must be owned by invoking user and not writable by others")
    if not 0 < info.st_size <= MAX_VAULT_BYTES:
        raise Blocked("Vault file size outside permitted limits")
    ciphertext = path.read_bytes()
    if not hmac.compare_digest(hashlib.sha256(ciphertext).hexdigest(), sha256.lower()):
        raise Blocked("Vault SHA-256 does not match trusted release")
    split_ciphertext(ciphertext)
    return ciphertext


def main(argv=None, *, read_password=getpass.getpass) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vault-file", type=Path, required=True)
    parser.add_argument("--vault-sha256", required=True)
    args = parser.parse_args(argv)
    if os.geteuid() != 0:
        raise Blocked("Gate must run as root on PVE")
    if not Path("/etc/pve/user.cfg").exists():
        raise Blocked("PVE host identity missing")
    vault = read_pinned_vault(args.vault_file, args.vault_sha256)
    print("VAULT_RELEASE_SHA256=PASS", flush=True)
    password = read_password("Fleet master password (hidden): ")
    if not 8 <= len(password) <= 1024:
        raise Blocked("Master password validation failed")
    valid = password_authenticates(vault, password.encode("utf-8"))
    del password
    if not valid:
        raise Blocked("Master password validation failed")
    print("MASTER_PASSWORD_HMAC=PASS", flush=True)
    print("GATE_MODE=READ_ONLY", flush=True)
    print("ANSIBLE_DECRYPT_AND_SITE_VALIDATION=PENDING", flush=True)
    print("PVE_GUEST_MODIFICATIONS=NONE", flush=True)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Blocked as error:
        print(f"MASTER_GATE=BLOCKED: {error}", file=sys.stderr)
        sys.exit(2)
