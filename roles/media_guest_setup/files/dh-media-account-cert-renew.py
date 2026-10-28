#!/usr/bin/env python3
"""Renew only the existing Media Server leaf certificate using its local CA.

Never rotates the trusted local CA, never changes guest passwords. Designed
for a root-owned systemd daily timer, no Ansible or Semaphore at runtime.
"""
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile

PKI = Path("/etc/digitalhouses/media-account-pki")
CERT = PKI / "server.crt"
CA = PKI / "ca.crt"
KEY = PKI / "ca.key"
CSR = PKI / "server.csr"
EXT = PKI / "server.ext"


def run(*args):
    return subprocess.run(args, capture_output=True, text=True, timeout=25, check=True)


def main():
    if os.geteuid() != 0:
        raise RuntimeError("ROOT_REQUIRED")
    for path in (CERT, CA, KEY, CSR, EXT):
        mode = path.lstat().st_mode
        if not stat.S_ISREG(mode) or path.is_symlink():
            raise RuntimeError("PKI_FILE_UNSAFE")
    # 30 days of margin before a known 397-day certificate expires.
    result = subprocess.run(
        ["openssl", "x509", "-checkend", "2592000", "-noout", "-in", str(CERT)],
        capture_output=True, timeout=10, check=False)
    if result.returncode == 0:
        print("MEDIA_ACCOUNT_TLS=VALID")
        return
    fd, temp = tempfile.mkstemp(prefix=".dh-leaf-", suffix=".crt", dir=PKI)
    os.close(fd)
    try:
        run("openssl", "x509", "-req", "-in", str(CSR), "-CA", str(CA),
            "-CAkey", str(KEY), "-out", temp, "-days", "397",
            "-sha256", "-extfile", str(EXT))
        run("openssl", "verify", "-CAfile", str(CA), temp)
        os.chmod(temp, 0o644)
        os.replace(temp, CERT)
        run("nginx", "-t")
        run("systemctl", "reload", "nginx.service")
        print("MEDIA_ACCOUNT_TLS=RENEWED")
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        print("MEDIA_ACCOUNT_TLS=RENEWAL_FAILED", file=sys.stderr)
        sys.exit(2)
