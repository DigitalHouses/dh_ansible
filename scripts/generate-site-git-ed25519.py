#!/usr/bin/env python3
"""Produce a fresh OpenSSH Ed25519 keypair for Ansible's no_log controller.

The private key exists only in this process's memory and the no_log Ansible
result until it is written to native Semaphore Key Store through localhost
HTTPS. Never run this tool directly in a terminal or log its stdout.
Semaphore 2.20's built-in generator produces RSA-2048 keys, which GitHub
stopped accepting for new SSH keys on 2026-10-14 (minimum RSA 3072).
"""
import json
import sys

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519


def create_keypair():
    key = ed25519.Ed25519PrivateKey.generate()
    private = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.OpenSSH,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")
    public = key.public_key().public_bytes(
        encoding=serialization.Encoding.OpenSSH,
        format=serialization.PublicFormat.OpenSSH,
    ).decode("ascii")
    assert public.startswith("ssh-ed25519 ")
    return {"private_key": private, "public_key": public}


def main():
    json.dump(create_keypair(), sys.stdout, separators=(",", ":"))
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
