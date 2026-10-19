#!/usr/bin/env python3
"""Render a SHA-pinned one-command launcher without altering its trusted code."""
from __future__ import annotations
import argparse
import hashlib
from pathlib import Path
import re

TEMPLATE = Path(__file__).with_name("semaphore-launch.sh")


def render(text, commit, fleet_commit):
    if not re.fullmatch(r"[a-f0-9]{40}", commit):
        raise ValueError("Release requires a 40-character immutable git SHA")
    if not re.fullmatch(r"[a-f0-9]{40}", fleet_commit):
        raise ValueError("Fleet requires a 40-character immutable Git SHA")
    if text.count("__RELEASE_COMMIT__") != 1 or text.count("__FLEET_COMMIT__") != 1:
        raise ValueError("Ambiguous launcher template")
    return (text.replace("__RELEASE_COMMIT__", commit)
                .replace("__FLEET_COMMIT__", fleet_commit)).encode()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--fleet-commit", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args=parser.parse_args()
    content=render(TEMPLATE.read_text(), args.commit, args.fleet_commit)
    if args.output.exists() or args.output.is_symlink():
        raise ValueError("Release file exists, refusing to overwrite")
    args.output.write_bytes(content)
    args.output.chmod(0o700)
    print("RELEASE_LAUNCHER_SHA256="+hashlib.sha256(content).hexdigest())


if __name__ == "__main__":
    main()
