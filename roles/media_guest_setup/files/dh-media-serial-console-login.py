#!/usr/bin/env python3
"""Provision protected Debian serial-console login; never print its secret in setup.

Run as root on the guest. --ensure generates an independent console password only
on first install; --check is safe for automation; --show is ONLY for root-only PVE
credential retrieval through QGA. Not tied to media owner/web password rotation.
"""
import argparse
import os
from pathlib import Path
import pwd
import re
import secrets
import stat
import string
import subprocess
import sys

ACCOUNT = "debian"
CREDENTIAL = Path("/root/.dh-media-console-login")
ALPHABET = string.ascii_letters + string.digits


def require(condition, reason):
    if not condition:
        raise RuntimeError(reason)


def run(*argv):
    return subprocess.run(argv, text=True, stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL, timeout=10, check=True).stdout


def login_ready():
    user = pwd.getpwnam(ACCOUNT)
    require(user.pw_uid == 1000 and user.pw_shell == "/bin/bash", "CONSOLE_ACCOUNT_DRIFT")
    status = run("/usr/bin/passwd", "-S", ACCOUNT).split()
    require(len(status) > 1 and status[1] == "P", "CONSOLE_PASSWORD_LOCKED")
    # Do not accidentally enable an SSH password login to a sudo-capable account.
    ssh = run("/usr/sbin/sshd", "-T").splitlines()
    require("passwordauthentication no" in ssh, "SSH_PASSWORD_AUTH_MUST_BE_OFF")
    require("kbdinteractiveauthentication no" in ssh, "SSH_INTERACTIVE_AUTH_MUST_BE_OFF")


def read_credential():
    info = CREDENTIAL.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_uid == 0
            and stat.S_IMODE(info.st_mode) == 0o600 and info.st_nlink == 1,
            "CONSOLE_CREDENTIAL_UNSAFE")
    lines = CREDENTIAL.read_text().splitlines()
    require(len(lines) == 2 and lines[0] == "username=" + ACCOUNT
            and re.fullmatch(r"password=[A-Za-z0-9]{20}", lines[1]),
            "CONSOLE_CREDENTIAL_INVALID")
    return lines


def ensure():
    require("console=ttyS0" in Path("/proc/cmdline").read_text(),
            "SERIAL_KERNEL_CONSOLE_MISSING")
    run("/usr/bin/systemctl", "is-active", "serial-getty@ttyS0.service")
    if CREDENTIAL.exists() or CREDENTIAL.is_symlink():
        read_credential()
        login_ready()
        print("MEDIA_SERIAL_CONSOLE=READY")
        return
    user = pwd.getpwnam(ACCOUNT)
    require(user.pw_uid == 1000 and user.pw_shell == "/bin/bash", "CONSOLE_ACCOUNT_DRIFT")
    ssh = run("/usr/sbin/sshd", "-T").splitlines()
    require("passwordauthentication no" in ssh, "SSH_PASSWORD_AUTH_MUST_BE_OFF")
    require("kbdinteractiveauthentication no" in ssh, "SSH_INTERACTIVE_AUTH_MUST_BE_OFF")
    password = "".join(secrets.choice(ALPHABET) for _ in range(20))
    fd = os.open(CREDENTIAL, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write("username=" + ACCOUNT + "\npassword=" + password + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        subprocess.run(["/usr/sbin/chpasswd"], input=ACCOUNT + ":" + password + "\n",
                       text=True, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=10, check=True)
    except BaseException:
        CREDENTIAL.unlink(missing_ok=True)
        raise
    login_ready()
    print("MEDIA_SERIAL_CONSOLE=READY")


def main():
    require(os.geteuid() == 0, "ROOT_REQUIRED")
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--ensure", action="store_true")
    group.add_argument("--check", action="store_true")
    group.add_argument("--show", action="store_true")
    args = parser.parse_args()
    if args.ensure:
        ensure()
        return
    lines = read_credential()
    login_ready()
    if args.check:
        print("MEDIA_SERIAL_CONSOLE=AVAILABLE")
    else:
        # Root-only execution; the caller must never capture this in task logs.
        for line in lines:
            print(line)


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, ValueError, KeyError,
            subprocess.CalledProcessError, subprocess.TimeoutExpired):
        print("MEDIA_SERIAL_CONSOLE=UNAVAILABLE", file=sys.stderr)
        sys.exit(2)
