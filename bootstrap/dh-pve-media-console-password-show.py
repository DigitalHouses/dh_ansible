#!/usr/bin/env python3
"""DigitalHouses Media Server serial console password recovery, root PVE console only.

Run: /usr/local/sbin/dh-media-console-password-show [--check]
Uses the QEMU Guest Agent; never stores the guest serial console password on PVE.
Only exact managed VM 2238 is allowed, not an arbitrary supplied VMID.
"""
import argparse
import json
import os
import re
import subprocess
import sys

VMID = "2238"
VMNAME = "mediaserver-serzhan"
QM = "/usr/sbin/qm"
GUEST_HELPER = "/usr/local/sbin/dh-media-serial-console-login"


def run(*argv, timeout=20):
    item = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=True)
    return item.stdout


def recover(check):
    if os.geteuid() != 0:
        raise PermissionError("PVE_ROOT_REQUIRED")
    status = run(QM, "status", VMID)
    if status.strip() != "status: running":
        raise RuntimeError("MEDIA_VM_NOT_RUNNING")
    config = run(QM, "config", VMID)
    if not re.search(r"(?m)^name: " + re.escape(VMNAME) + r"$", config):
        raise RuntimeError("MEDIA_VM_IDENTITY_DRIFT")
    result = json.loads(run(QM, "guest", "exec", VMID, "--", GUEST_HELPER,
                            *(["--check"] if check else []), timeout=30))
    if result.get("exited") != 1 or result.get("exitcode") != 0 or result.get("out-truncated", 0) != 0:
        raise RuntimeError("GUEST_CREDENTIAL_CHECK_FAILED")
    output = result.get("out-data", "")
    if check:
        if output.strip() != "MEDIA_SERIAL_CONSOLE=AVAILABLE":
            raise RuntimeError("UNEXPECTED_CHECK_RESULT")
        print("MEDIA_SERIAL_CONSOLE_PASSWORD=AVAILABLE")
        return
    lines = output.splitlines()
    if len(lines) != 2 or not re.fullmatch("username=debian", lines[0]):
        raise RuntimeError("UNEXPECTED_GUEST_CREDENTIALS")
    if not re.fullmatch("password=[A-Za-z0-9]{20}", lines[1]):
        raise RuntimeError("UNEXPECTED_GUEST_CREDENTIALS")
    # Secrets are emitted ONLY to the requesting root terminal. Never log.
    print(lines[0])
    print(lines[1])


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    try:
        recover(args.check)
    except (OSError, ValueError, RuntimeError, PermissionError,
            subprocess.CalledProcessError, subprocess.TimeoutExpired):
        print("MEDIA_SERIAL_CONSOLE_PASSWORD=UNAVAILABLE", file=sys.stderr)
        sys.exit(2)
