#!/usr/bin/env python3
"""Fresh ONLY: Debian 13 Semaphore/Ansible runtime inside verified CT1231.

Does not touch PVE, TrueNAS, external VM/LXC, GitHub or Fleet Vault.
Must be staged by a verified PVE bootstrap into a new empty container.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import locale
import os
from pathlib import Path
import pwd
import re
import secrets
import stat
import subprocess
import sys
import urllib.request
import urllib.error
import ssl
import time

SEMAPHORE_VERSION = "2.19.16"
SEMAPHORE_DEB_SHA256 = "56c4ac99b712b3fde11a7d600a0789870a3b945bab0c12f41d91864e0aeb85b8"
SEMAPHORE_URL = ("https://github.com/semaphoreui/semaphore/releases/download/v"
                 + SEMAPHORE_VERSION + "/semaphore_" + SEMAPHORE_VERSION
                 + "_linux_amd64.deb")
CONFIG = Path("/etc/semaphore/config.json")
BASE = Path("/var/lib/semaphore")
CERT = Path("/etc/semaphore/tls.crt")
KEY = Path("/etc/semaphore/tls.key")
ADMIN_SECRET = Path("/root/dh-semaphore-initial-admin-password")
UNIT = Path("/etc/systemd/system/digitalhouses-semaphore.service")
VENV = Path("/opt/ansible/venv")
APT_PKGS = ("python3", "python3-venv", "python3-pip", "ca-certificates", "curl",
            "openssl", "git", "openssh-client", "tzdata", "avahi-daemon", "avahi-utils")


class Blocked(RuntimeError):
    pass


def command(*argv, timeout=180, quiet=True):
    try:
        p = subprocess.run(argv, capture_output=quiet, text=True, timeout=timeout,
                           check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise Blocked(f"Command unavailable/timed out: {argv[0]}") from None
    if p.returncode:
        raise Blocked(f"Command failed: {Path(argv[0]).name}, rc={p.returncode}")
    return p.stdout.strip() if quiet else ""


def standardize_locale():
    """Debian 13 has C.UTF-8 without a locale-gen dependency."""
    try:
        locale.setlocale(locale.LC_ALL, "C.UTF-8")
    except locale.Error as error:
        raise Blocked("C.UTF-8 locale unavailable; do not install Ansible") from error
    os.environ["LANG"] = "C.UTF-8"
    os.environ["LC_ALL"] = "C.UTF-8"
    os.environ.pop("LANGUAGE", None)
    print("GUEST_LOCALE=PASS C.UTF-8", flush=True)


def prepare_ansible_runtime_access():
    """Root-owned venv code must be readable by the non-root Semaphore service."""
    for directory in (VENV.parent, VENV):
        if (directory.is_symlink() or not directory.is_dir()
                or directory.stat().st_uid != 0):
            raise Blocked("Unexpected Ansible runtime owner or symlink")
    os.chmod(VENV.parent, 0o755)
    # Dedicated, root-owned Ansible virtualenv contains executable code, not credentials.
    command("chmod", "-R", "a+rX", str(VENV), timeout=120)
    print("ANSIBLE_RUNTIME_PERMISSIONS=PASS", flush=True)


def validate(site, runner_ip, *, resume_ansible=False):
    if os.geteuid() != 0 or Path("/etc/pve/user.cfg").exists():
        raise Blocked("Run as root inside LXC, not on PVE host")
    if not re.fullmatch(r"[a-z][a-z0-9_-]{0,30}", site):
        raise Blocked("Invalid site")
    if command("hostname") != f"ansible-{site}":
        raise Blocked("Runner hostname mismatch")
    if not re.fullmatch(r"(?:[0-9]{1,3}\.){3}231", runner_ip):
        raise Blocked("Nonstandard runner IP")
    net = command("ip", "-4", "-o", "addr", "show")
    if runner_ip not in [part.split("/")[0] for line in net.splitlines()
                         for part in line.split() if "/" in part]:
        raise Blocked("Runner IP is not assigned")
    if (CONFIG.exists() or ADMIN_SECRET.exists() or UNIT.exists()
            or Path("/var/lib/semaphore/database.sqlite").exists()):
        raise Blocked("Fresh-only installer cannot adopt or overwrite Semaphore")
    if Path("/opt/digitalhouses/setup-wizard").exists():
        raise Blocked("Legacy Web Wizard detected")
    if resume_ansible:
        if (not VENV.is_dir() or VENV.is_symlink()
                or not (VENV / "bin/ansible-playbook").is_file()):
            raise Blocked("Resume requested without intact Ansible virtualenv")
        if (VENV.stat().st_uid != 0 or VENV.parent.stat().st_uid != 0
                or VENV.parent.is_symlink()):
            raise Blocked("Resume virtualenv ownership mismatch")
        print("SEMAPHORE_GUEST_RESUME=EXISTING_ANSIBLE_ONLY", flush=True)
    elif VENV.exists() or VENV.is_symlink():
        raise Blocked("Virtualenv already exists; use controlled resume after audit")
    print("SEMAPHORE_GUEST_PREFLIGHT=PASS", flush=True)


def password_file(password):
    fd = os.open(ADMIN_SECRET, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w") as stream:
        stream.write(password + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def setup_files(site, runner_ip, service):
    CONFIG.parent.mkdir(mode=0o750, exist_ok=True)
    os.chown(CONFIG.parent, 0, service.pw_gid)
    os.chmod(CONFIG.parent, 0o750)
    BASE.mkdir(mode=0o700, exist_ok=True)
    (BASE / "tmp").mkdir(mode=0o700, exist_ok=True)
    for p in (BASE, BASE / "tmp"):
        os.chown(p, service.pw_uid, service.pw_gid)
        os.chmod(p, 0o700)
    # Locally self-signed for initial installation; the operator must check fingerprint.
    command("openssl", "req", "-x509", "-newkey", "ec",
            "-pkeyopt", "ec_paramgen_curve:P-256", "-nodes", "-days", "365",
            "-keyout", str(KEY), "-out", str(CERT),
            "-subj", f"/CN=ansible-{site}",
            "-addext", f"subjectAltName=IP:{runner_ip},IP:127.0.0.1",
            timeout=60)
    for file, mode in ((CERT, 0o640), (KEY, 0o600)):
        os.chown(file, service.pw_uid, service.pw_gid)
        os.chmod(file, mode)
    def secret():
        return base64.b64encode(secrets.token_bytes(32)).decode()
    config = {
        "dialect": "sqlite",
        "sqlite": {"host": str(BASE / "database.sqlite")},
        "port": ":3000",
        "interface": "0.0.0.0",
        "web_host": f"https://{runner_ip}:3000",
        "tmp_path": str(BASE / "tmp"),
        "cookie_hash": secret(),
        "cookie_encryption": secret(),
        "access_key_encryption": secret(),
        "tls": {"enabled": True, "cert_file": str(CERT), "key_file": str(KEY)}
    }
    fd = os.open(CONFIG, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o640)
    with os.fdopen(fd, "w") as file:
        json.dump(config, file, indent=2)
        file.write("\n")
        file.flush()
        os.fsync(file.fileno())
    os.chown(CONFIG, 0, service.pw_gid)
    os.chmod(CONFIG, 0o640)


def configure_network_identity(site: str, runner_ip: str) -> None:
    """Publish the verified system hostname through native Debian mDNS.

    No NetAlertX-specific code or per-site static host entries. avahi-daemon
    uses the existing kernel hostname, and refuses an occupied .local name.
    """
    fqdn = f"ansible-{site}.local"
    if command("hostname") != f"ansible-{site}":
        raise Blocked("Network identity does not match canonical hostname")
    command("systemctl", "enable", "--now", "avahi-daemon.service", timeout=45)
    if command("systemctl", "is-active", "avahi-daemon.service") != "active":
        raise Blocked("mDNS publisher is inactive")
    result = command("avahi-resolve-host-name", "-4", fqdn, timeout=20).split()
    if len(result) != 2 or result[0].rstrip(".") != fqdn or result[1] != runner_ip:
        raise Blocked("mDNS address does not match pinned site identity")
    print(f"NETWORK_IDENTITY_MDNS=PASS fqdn={fqdn} ip={runner_ip}", flush=True)


def provision(site, runner_ip, requirements, *, resume_ansible=False):
    print("OS_DEPENDENCIES=START", flush=True)
    if not requirements.is_file() or requirements.is_symlink():
        raise Blocked("Pinned runner requirements missing")
    if not resume_ansible:
        # Debian APT may exit 0 even after failed DNS/downloads; fail on any index error.
        command("apt-get", "update", "-o", "APT::Update::Error-Mode=any",
                "-o", "Acquire::Retries=2", timeout=240)
        command("env", "DEBIAN_FRONTEND=noninteractive", "apt-get", "install",
                "-y", "--no-install-recommends", *APT_PKGS, timeout=300)
        configure_network_identity(site, runner_ip)
        command("python3", "-m", "venv", str(VENV), timeout=90)
        command(str(VENV / "bin/pip"), "install", "--disable-pip-version-check",
                "-r", str(requirements), timeout=300)
    if resume_ansible:
        configure_network_identity(site, runner_ip)
    command(str(VENV / "bin/python"), "-m", "pip", "check", timeout=60)
    ansible = command(str(VENV / "bin/ansible-playbook"), "--version")
    if "ansible-playbook [core 2.21.4]" not in ansible:
        raise Blocked("Ansible Core 2.21.4 verify failed")
    prepare_ansible_runtime_access()
    print("ANSIBLE_RUNTIME=PASS", flush=True)

    release = Path("/root/dh-semaphore-2.19.16.deb")
    if release.exists() or release.is_symlink():
        raise Blocked("Stale Semaphore release file")
    # HTTPS verifies the upstream server; pinned SHA verifies exact release bytes.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(SEMAPHORE_URL, timeout=90) as response, release.open("xb") as output:
        remaining = 64 * 1024 * 1024
        while True:
            block = response.read(min(1024 * 1024, remaining + 1))
            if not block:
                break
            remaining -= len(block)
            if remaining < 0:
                raise Blocked("Semaphore package exceeds 64 MiB cap")
            output.write(block)
    sha = hashlib.sha256(release.read_bytes()).hexdigest()
    if sha != SEMAPHORE_DEB_SHA256:
        raise Blocked("Semaphore release SHA256 mismatch")
    command("env", "DEBIAN_FRONTEND=noninteractive", "apt-get", "install",
            "-y", str(release), timeout=180)
    release.unlink()
    if SEMAPHORE_VERSION not in command("semaphore", "version"):
        raise Blocked("Semaphore version mismatch")
    if subprocess.run(["systemctl", "is-active", "--quiet", "semaphore.service"]).returncode == 0:
        raise Blocked("Conflicting Semaphore service active")
    print("SEMAPHORE_PACKAGE=PASS", flush=True)
    print("SEMAPHORE_TLS_SETUP=START", flush=True)

    if not Path("/usr/sbin/nologin").exists():
        raise Blocked("nologin shell unavailable")
    try:
        service = pwd.getpwnam("semaphore")
    except KeyError:
        command("useradd", "--system", "--user-group", "--home-dir", str(BASE),
                "--shell", "/usr/sbin/nologin", "semaphore")
        service = pwd.getpwnam("semaphore")
    if service.pw_uid == 0:
        raise Blocked("Unsafe service user")
    setup_files(site, runner_ip, service)
    print("SEMAPHORE_TLS_SETUP=PASS", flush=True)
    command("runuser", "-u", "semaphore", "--", "/usr/bin/semaphore",
            "migrate", "--config", str(CONFIG), timeout=90)
    print("SEMAPHORE_DB_MIGRATION=PASS", flush=True)
    password = secrets.token_urlsafe(32)
    password_file(password)
    # Upstream user CLI requires --password; ephemeral process argv exists only
    # inside this trusted guest. Owner should rotate via HTTPS web UI after setup.
    command("runuser", "-u", "semaphore", "--", "/usr/bin/semaphore",
            "users", "add", "--config", str(CONFIG), "--admin",
            "--login", "admin", "--name", "DigitalHouses Administrator",
            "--email", "admin@localhost", "--password", password, timeout=90)
    del password
    print("SEMAPHORE_ADMIN_CREATE=PASS", flush=True)

    unit = """[Unit]
Description=DigitalHouses Semaphore UI
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=semaphore
Group=semaphore
Environment=PATH=/opt/ansible/venv/bin:/usr/local/bin:/usr/bin:/bin
Environment=LANG=C.UTF-8
Environment=LC_ALL=C.UTF-8
ExecStart=/usr/bin/semaphore server --config /etc/semaphore/config.json
Restart=on-failure
RestartSec=5
NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths=/var/lib/semaphore
ProtectHome=true
PrivateTmp=true
UMask=0077

[Install]
WantedBy=multi-user.target
"""
    fd = os.open(UNIT, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    with os.fdopen(fd, "w") as stream:
        stream.write(unit)
    os.chmod(UNIT, 0o644)
    command("systemctl", "daemon-reload")
    command("systemctl", "enable", "--now", UNIT.name, timeout=90)
    if command("systemctl", "is-active", UNIT.name) != "active":
        raise Blocked("Semaphore unit is inactive")
    print("SEMAPHORE_SYSTEMD=PASS", flush=True)
    ctx = ssl.create_default_context(cafile=str(CERT))
    opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx),
                                         urllib.request.ProxyHandler({}))
    # systemd active means the process has started, not that its TLS listener is ready.
    # Use bounded polling, preserving certificate validation and rejecting an unhealthy API.
    deadline = time.monotonic() + 60
    while True:
        try:
            with opener.open("https://127.0.0.1:3000/api/ping", timeout=4) as reply:
                if reply.status == 200:
                    break
        except (urllib.error.URLError, TimeoutError, OSError):
            pass
        if time.monotonic() >= deadline:
            raise Blocked("Semaphore HTTPS endpoint not ready within 60 seconds")
        time.sleep(2)
    print("SEMAPHORE_HTTPS=PASS", flush=True)
    fingerprint = hashlib.sha256(ssl.PEM_cert_to_DER_cert(CERT.read_text())).hexdigest()
    print("TLS_CERT_SHA256=" + ":".join(fingerprint[i:i+2] for i in range(0,64,2)).upper())
    print(f"SEMAPHORE_URL=https://{runner_ip}:3000/")
    print(f"INITIAL_ADMIN_PASSWORD_FILE={ADMIN_SECRET} (root:root 0600)")
    print("PASSWORD_ROTATION_REQUIRED=YES")
    print("SEMAPHORE_PROJECT_SEED=PENDING")
    print("DONE")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site", required=True)
    parser.add_argument("--runner-ip", required=True)
    parser.add_argument("--requirements", type=Path, required=True)
    parser.add_argument("--resume-ansible", action="store_true", help="Resume verified Ansible-only partial install")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    standardize_locale()
    validate(args.site, args.runner_ip, resume_ansible=args.resume_ansible)
    if not args.apply:
        print("MODE=READ_ONLY; no packages/services changed")
        return 0
    provision(args.site, args.runner_ip, args.requirements,
              resume_ansible=args.resume_ansible)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Blocked as err:
        print(f"INSTALL=BLOCKED: {err}", file=sys.stderr)
        sys.exit(2)
