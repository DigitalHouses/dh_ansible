#!/usr/bin/env python3
"""One PVE command -> owner GitHub auth/Vault gate -> NEW runner -> Semaphore UI.

SECURITY: private git is authenticated through owner device OAuth on github.com.
This script MUST come from an independently verified immutable release;
--expected-commit pins the trusted fleet source. Never run on an occupied CTID.
No root secrets are removed from PVE host.
"""
from __future__ import annotations

import argparse
import base64
import getpass
import hashlib
import importlib.util
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import tarfile
import time
import urllib.request

CTID = 1231
CODE_REPO = "DigitalHouses/dh_ansible"
FLEET_REPO = "DigitalHouses/dh_ansible_fleet_detail"
BRIDGE = "vmbr0"
SITE_INVENTORY_BASE = "/var/lib/ansimble/site-data"
ROOT_STAGE = Path("/var/lib/digitalhouses/bootstrap")
CODE_FILES = (
    "bootstrap/pve-identities.py",
    "bootstrap/import-pve-identities-vault.py",
    "bootstrap/fleet-master-gate.py",
    "bootstrap/semaphore-guest-install.py",
    "bootstrap/semaphore-project.py",
    "requirements/runner.txt",
    "playbooks/apply-runner-remote-commander.yml",
    "roles/runner_remote_commander/defaults/main.yml",
    "roles/runner_remote_commander/tasks/main.yml",
    "roles/runner_remote_commander/handlers/main.yml",
    "roles/runner_remote_commander/templates/digitalhouses-remote-commander.service.j2",
    "bootstrap/remote-commander-install-request.sh",
    "bootstrap/dh-remote-commander-install.service",
    "bootstrap/dh-remote-commander-install.path",
)
REMOTE_INSTALL_FILES = tuple(item for item in CODE_FILES
                             if item.startswith(("playbooks/apply-runner-remote-commander.yml",
                                                 "roles/runner_remote_commander/")))
REMOTE_BOOTSTRAP_DIR = "/opt/digitalhouses/bootstrap/remote-commander"
EXPECTED_NAME = "ansible"


def runner_hostname(site: str) -> str:
    """Canonical LXC hostname is ansible-<site_id> for unique site identity.

    The <owner>-<type> Site ID and PRIVATE Fleet metadata are verified
    before any guest creation.
    """
    match = re.fullmatch(r"([a-z][a-z0-9]*(?:-[a-z0-9]+)*)-([a-z][a-z0-9]*)", site)
    if not match:
        raise Blocked("Canonical site ID must be <owner>-<type>")
    return f"{EXPECTED_NAME}-{site}"
GH_VERSION = "2.102.0"
GH_URL = "https://github.com/cli/cli/releases/download/v2.102.0/gh_2.102.0_linux_amd64.tar.gz"
GH_SHA256 = "bb766f710eef8ede859c18578c72c327597cd4c8a85b06001b1f3843c6019386"


class Blocked(RuntimeError):
    pass


def safe_guest_failure_lines(argv, stdout, stderr):
    """Allowlisted installation errors only; never dump arbitrary stdout/stderr."""
    if not argv or argv[0] != "pct":
        return []
    if not any(str(arg).endswith("/semaphore-guest-install.py") for arg in argv):
        return []
    lines = (stdout or "").splitlines() + (stderr or "").splitlines()
    allowed = re.compile(r"^(?:INSTALL=BLOCKED: Command failed: [a-zA-Z0-9_.-]+, rc=[0-9]+|INSTALL=BLOCKED: Runner hostname mismatch|SEMAPHORE_GUEST_PREFLIGHT=PASS|SEMAPHORE_GUEST_RESUME=EXISTING_ANSIBLE_ONLY|GUEST_LOCALE=PASS C\.UTF-8|OS_DEPENDENCIES=START|NETWORK_IDENTITY_MDNS=PASS fqdn=[a-z0-9.-]+ ip=[0-9.]+|ANSIBLE_RUNTIME_PERMISSIONS=PASS|ANSIBLE_RUNTIME=PASS|SEMAPHORE_PACKAGE=PASS|SEMAPHORE_TLS_SETUP=(?:START|PASS)|SEMAPHORE_DB_MIGRATION=PASS|SEMAPHORE_ADMIN_CREATE=PASS|SEMAPHORE_SYSTEMD=PASS)$")
    return [line for line in lines if len(line) < 180 and allowed.fullmatch(line)][-8:]

def run(*argv, timeout=120, env=None, reveal=False):
    try:
        p = subprocess.run(argv, timeout=timeout, check=False,
                           text=True, capture_output=not reveal, env=env)
    except (OSError, subprocess.TimeoutExpired):
        raise Blocked(f"Command unavailable or timed out: {argv[0]}") from None
    if p.returncode:
        # Never echo arbitrary subprocess arguments: some callers handle secrets.
        if (len(argv) >= 5 and argv[:3] == ("pct", "exec", str(CTID))
                and argv[3] == "--" and
                Path(argv[4]).name in {"install", "stat", "sha256sum", "rm", "test"}):
            print("PCT_EXEC_FAILURE=command:" + Path(argv[4]).name +
                  f" rc:{p.returncode}", flush=True)
        for diagnostic in safe_guest_failure_lines(argv, p.stdout, p.stderr):
            print("GUEST_DIAGNOSTIC: " + diagnostic, flush=True)
        raise Blocked(f"Command failed: {Path(argv[0]).name}, rc={p.returncode}")
    return p.stdout.strip() if not reveal else ""


def code(*argv, timeout=20):
    try:
        return subprocess.run(argv, capture_output=True, timeout=timeout).returncode
    except (OSError, subprocess.TimeoutExpired):
        raise Blocked(f"Cannot determine state: {argv[0]}") from None


def template_sort_key(name):
    version = name.removeprefix("debian-13-standard_").removesuffix("_amd64.tar.zst")
    return tuple((1, int(s)) if s.isdigit() else (0, s)
                 for s in re.findall(r"\d+|\D+", version))


def select_template(listing):
    names = sorted(set(re.findall(
        r"debian-13-standard_[A-Za-z0-9.+-]+_amd64\.tar\.zst", listing)),
        key=template_sort_key)
    if not names:
        raise Blocked("Official Debian 13 standard template missing from local storage")
    return "local:vztmpl/" + names[-1]



def ensure_official_debian_template():
    """Resolve latest official Debian 13 via PVE, download if absent, verify local archive.

    Called only after owner authentication, Fleet Vault gate and CT creation approval.
    The PVE appliance itself validates the catalog download checksum.
    """
    run("pveam", "update", timeout=180)
    available = run("pveam", "available", "--section", "system", timeout=60)
    official = select_template(available)
    filename = official.split("/", 1)[1]
    installed = run("pveam", "list", "local")
    if official not in installed.split():
        print(f"DEBIAN_TEMPLATE_DOWNLOAD=START {filename}", flush=True)
        run("pveam", "download", "local", filename, timeout=600)
    else:
        print(f"DEBIAN_TEMPLATE_CACHE=HIT {filename}", flush=True)
    # Reject unexpected files, links and incomplete downloads.
    archive = Path("/var/lib/vz/template/cache") / filename
    if (archive.is_symlink() or not archive.is_file()
            or archive.stat().st_size < 10_000_000):
        raise Blocked("Debian template archive missing or invalid after PVE download")
    if official not in run("pveam", "list", "local").split():
        raise Blocked("Debian template not registered in local PVE storage")
    print(f"DEBIAN_TEMPLATE=VERIFIED {official}", flush=True)
    return official


def host_preflight(site, cidr, gateway, pve_ip, *, resume=False, resume_ansible=False,
                   resume_semaphore=False, resume_fresh=False):
    if os.geteuid() != 0 or not Path("/etc/pve/user.cfg").exists():
        raise Blocked("Requires root on genuine PVE")
    if not re.fullmatch(r"[a-z][a-z0-9_-]{0,30}", site):
        raise Blocked("Invalid site identifier")
    ip = ipaddress.IPv4Interface(cidr)
    gateway_addr = ipaddress.IPv4Address(gateway)
    host_addr = ipaddress.IPv4Address(pve_ip)
    if (int(ip.ip) & 255) != 231 or gateway_addr not in ip.network or host_addr not in ip.network:
        raise Blocked("Site runner addressing invalid")
    if gateway_addr == ip.ip or host_addr == ip.ip:
        raise Blocked("Runner IP collides with PVE/gateway")
    ip_lines = run("ip", "-4", "-o", "addr", "show")
    if pve_ip not in [part.split("/")[0] for line in ip_lines.splitlines()
                      for part in line.split() if "/" in part]:
        raise Blocked("This is not the expected PVE host IP")
    if not Path("/sys/class/net/vmbr0").exists():
        raise Blocked("vmbr0 missing")
    if resume or resume_ansible or resume_semaphore or resume_fresh:
        existing = run("pct", "config", str(CTID))
        if code("qm", "config", str(CTID)) == 0:
            raise Blocked("VM1231 exists; refusing LXC resume")
        keys = dict(line.split(": ", 1) for line in existing.splitlines() if ": " in line)
        if (keys.get("hostname") != runner_hostname(site)
                or keys.get("unprivileged") != "1"
                or keys.get("ostype") != "debian"
                or keys.get("onboot") != "0"
                or keys.get("protection") != "1"
                or keys.get("cores") != "1"
                or keys.get("memory") != "1024"
                or keys.get("swap") != "512"
                or keys.get("features") != "nesting=1"
                or "local-lvm:vm-1231-disk-0,size=16G" != keys.get("rootfs")
                or not all(v in keys.get("net0", "") for v in
                           (f"ip={cidr}", f"gw={gateway}", "bridge=vmbr0", "name=eth0"))
                or any(re.fullmatch(r"(?:mp|net|hostpci|usb|scsi|sata|ide|virtio)\d+", k)
                       and k != "net0" for k in keys)
                or any(k.startswith("lxc.mount") or k == "hookscript" for k in keys)):
            raise Blocked("CT1231 identity/resources differ from fresh pilot; manual audit required")
        if run("pct", "status", str(CTID)) != "status: running":
            raise Blocked("CT1231 must be running for controlled resume")
        if pct_guest("hostname") != runner_hostname(site):
            raise Blocked("Guest hostname differs from expected site")
        guest_network = pct_guest("ip", "-4", "-o", "addr", "show", "dev", "eth0")
        if str(ip.ip) not in guest_network:
            raise Blocked("Guest IP differs from expected site")
        if not resume_fresh:
            pct_guest("test", "-f", "/opt/digitalhouses/bootstrap/semaphore-guest-install.py")
            pct_guest("test", "-f", "/opt/digitalhouses/bootstrap/runner.txt")
        if resume_semaphore:
            for marker in ("/opt/ansimble/venv/bin/ansible-playbook",
                           "/etc/semaphore/config.json",
                           "/etc/semaphore/tls.crt",
                           "/var/lib/semaphore/database.sqlite",
                           "/root/dh-semaphore-initial-admin-password",
                           "/etc/systemd/system/digitalhouses-semaphore.service",
                           "/usr/bin/semaphore",
                           "/opt/digitalhouses/bootstrap/semaphore-project.py"):
                pct_guest("test", "-f", marker)
            for marker in ("/root/dh-fleet-deploy-key",
                           "/var/lib/semaphore/.ssh/known_hosts",
                           "/root/dh-semaphore-2.19.16.deb"):
                if code("pct", "exec", str(CTID), "--", "test", "-e", marker) == 0:
                    raise Blocked("Semaphore resume has unexpected deployment or package artifacts")
            if pct_guest("dpkg-query", "-W", "-f=${Version}", "semaphore") != "2.19.16":
                raise Blocked("Semaphore version differs from pinned release")
            if pct_guest("systemctl", "is-active",
                         "digitalhouses-semaphore.service") != "active":
                raise Blocked("Semaphore service is not active")
            ready_probe = ('import ssl,urllib.request; '
                           'ctx=ssl.create_default_context(cafile="/etc/semaphore/tls.crt"); '
                           'response=urllib.request.urlopen('
                           '"https://127.0.0.1:3000/api/ping",context=ctx,timeout=6); '
                           'assert response.status==200')
            pct_guest("python3", "-c", ready_probe)
            print("CT1231=VERIFIED_RUNNING_SEMAPHORE", flush=True)
        else:
            for marker in ("/etc/semaphore/config.json",
                           "/var/lib/semaphore/database.sqlite",
                           "/root/dh-semaphore-initial-admin-password",
                           "/etc/systemd/system/digitalhouses-semaphore.service",
                           "/usr/bin/semaphore", "/root/dh-semaphore-2.19.16.deb"):
                if code("pct", "exec", str(CTID), "--", "test", "-e", marker) == 0:
                    raise Blocked("Guest has partial Semaphore runtime; recovery audit required")
        if resume_ansible:
            if code("pct", "exec", str(CTID), "--", "test", "-d",
                    "/opt/ansimble/venv") != 0:
                raise Blocked("Expected existing Ansible venv missing")
            version = pct_guest("env", "LC_ALL=C.UTF-8", "LANG=C.UTF-8",
                                "/opt/ansimble/venv/bin/ansible-playbook", "--version")
            if "ansible-playbook [core 2.21.4]" not in version:
                raise Blocked("Existing Ansible version differs from release")
            pct_guest("/opt/ansimble/venv/bin/python", "-m", "pip", "check")
            print("CT1231=VERIFIED_EXISTING_ANSIBLE_ONLY", flush=True)
        elif not resume_semaphore:
            if code("pct", "exec", str(CTID), "--", "test", "-e",
                    "/opt/ansimble/venv") == 0:
                raise Blocked("Guest has partial Ansible installation; use controlled resume")
            if resume_fresh:
                for marker in ("/opt/digitalhouses/bootstrap", "/opt/ansimble",
                               "/var/lib/semaphore", "/etc/semaphore",
                               "/root/dh-fleet-deploy-key"):
                    if code("pct", "exec", str(CTID), "--", "test", "-e", marker) == 0:
                        raise Blocked("Fresh resume guest has deployment artifacts")
                if pct_guest("stat", "-c", "%a:%u:%g:%F", "/etc") != "755:0:0:directory":
                    raise Blocked("Fresh resume requires repaired /etc")
                verify_fresh_system_bus()
                print("CT1231=VERIFIED_FRESH_PREBOOT_RUNNER", flush=True)
            else:
                print("CT1231=VERIFIED_EXISTING_EMPTY_RUNNER", flush=True)
    elif code("pct", "config", str(CTID)) == 0 or code("qm", "config", str(CTID)) == 0:
        raise Blocked("CT1231 / VM1231 already exists; no overwrite")
    storage = [line.split() for line in run("pvesm", "status").splitlines()]
    for name in ("local", "local-lvm"):
        if not any(len(r) > 2 and r[0] == name and r[2] == "active" for r in storage):
            raise Blocked("Required PVE storage inactive: " + name)
    try:
        template = select_template(run("pveam", "list", "local"))
    except Blocked:
        template = None  # Clean PVE: download only after explicit operator confirmation
        print("PVE_ESCROW=PASS (no secrets printed)", flush=True)
    if not (resume or resume_ansible or resume_semaphore or resume_fresh):
        print(f"CT1231=ABSENT; TEMPLATE={template or 'DOWNLOAD_REQUIRED'}", flush=True)
    print("PVE_HOST_PREFLIGHT=PASS", flush=True)
    return template


def ensure_ephemeral_gh(work: Path) -> Path:
    """Download and verify official CLI into root-owned ephemeral storage."""
    installed = shutil.which("gh")
    if installed:
        return Path(installed)
    archive = work / "gh.tar.gz"
    try:
        with urllib.request.urlopen(GH_URL, timeout=90) as response, archive.open("xb") as output:
            remaining = 32 * 1024 * 1024
            while True:
                chunk = response.read(min(1048576, remaining + 1))
                if not chunk:
                    break
                remaining -= len(chunk)
                if remaining < 0:
                    raise Blocked("GitHub CLI archive size limit exceeded")
                output.write(chunk)
    except OSError as error:
        raise Blocked("Failed to fetch official pinned GitHub CLI") from error
    if hashlib.sha256(archive.read_bytes()).hexdigest() != GH_SHA256:
        raise Blocked("Official GitHub CLI checksum mismatch")
    target = work / "bin"
    target.mkdir(mode=0o700)
    binary = target / "gh"
    with tarfile.open(archive, "r:gz") as stream:
        matches = [m for m in stream.getmembers() if m.name.endswith("/bin/gh")]
        if len(matches) != 1 or not matches[0].isfile() or not 1_000_000 < matches[0].size < 75_000_000:
            raise Blocked("Unexpected GitHub CLI archive structure")
        src = stream.extractfile(matches[0])
        if src is None:
            raise Blocked("GitHub CLI binary unavailable")
        fd = os.open(binary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o700)
        with os.fdopen(fd, "wb") as output:
            shutil.copyfileobj(src, output)
    archive.unlink()
    print("EPHEMERAL_GH=PASS pinned 2.102.0", flush=True)
    return binary

def git_env(configdir):
    env = os.environ.copy()
    env.update({"GH_CONFIG_DIR": str(configdir), "BROWSER": "false",
                "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_TERMINAL_PROMPT": "0"})
    env.pop("GH_TOKEN", None)
    env.pop("GITHUB_TOKEN", None)
    return env


def fetch_immutable_file(checkout, expected_sha, relative, env, *, repository):
    """GitHub Contents API reads only an exact Git commit: no personal token to git."""
    endpoint = f"repos/{repository}/contents/{relative}?ref={expected_sha}"
    response = run("gh", "api", endpoint, timeout=40, env=env)
    try:
        record = json.loads(response)
        if record.get("type") != "file" or record.get("encoding") != "base64":
            raise ValueError("Not a regular repository file")
        contents = base64.b64decode(record["content"], validate=False)
        git_blob = hashlib.sha1(
            b"blob " + str(len(contents)).encode() + b"\0" + contents).hexdigest()
        if git_blob != record.get("sha"):
            raise ValueError("Pinned GitHub blob SHA mismatch")
    except (ValueError, KeyError, TypeError):
        raise Blocked("Untrusted or invalid GitHub file: " + relative) from None
    dest = checkout / relative
    dest.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as output:
        output.write(contents)
    return dest


def authenticate_git(checkout, expected_sha, fleet_sha, env, site, *, preauthorized=False):
    if shutil.which("gh", path=env.get("PATH")) is None:
        raise Blocked("Verified GitHub CLI unavailable")
    if preauthorized:
        run("gh", "auth", "status", timeout=20, env=env)
        print("OWNER_GITHUB_SESSION=PASS (from verified launcher)", flush=True)
    else:
        print("OWNER_AUTH=https://github.com/login/device", flush=True)
        print("OWNER_ACTION=Approve GitHub device login with 2FA remotely", flush=True)
        run("gh", "auth", "login", "--web", "--git-protocol", "https",
            timeout=600, env=env, reveal=True)
    # Release and fleet metadata are independently pinned, never use floating main.
    for repository, commit in ((CODE_REPO, expected_sha), (FLEET_REPO, fleet_sha)):
        run("gh", "api", "repos/" + repository, timeout=30, env=env)
        data = json.loads(run("gh", "api", "repos/" + repository +
                              "/git/commits/" + commit, timeout=30, env=env))
        if data.get("sha") != commit:
            raise Blocked("Pinned commit identity check failed for " + repository)
    print("CODE_GIT_SHA=PASS " + expected_sha, flush=True)
    print("FLEET_GIT_SHA=PASS " + fleet_sha, flush=True)
    for item in CODE_FILES:
        fetch_immutable_file(checkout, expected_sha, item, env, repository=CODE_REPO)
    fleet = checkout.parent / "fleet-detail"
    fleet.mkdir(mode=0o700)
    fetch_immutable_file(fleet, fleet_sha, "secrets/fleet.vault.yml", env,
                         repository=FLEET_REPO)
    paths = fetch_site_inventory(checkout, fleet_sha, env, site)
    verify_site_identity(checkout, site)
    print("CODE_AND_FLEET_ARTIFACTS=PASS (two immutable commits)", flush=True)
    return paths



def validate_site_inventory_tree(entries, site):
    """Allow only immutable, ordinary YAML data from exactly one site subtree."""
    if not re.fullmatch(r"[a-z][a-z0-9_-]{0,30}", site):
        raise Blocked("Invalid site identifier")
    if not isinstance(entries, list) or len(entries) > 20000:
        raise Blocked("Invalid or oversized Fleet Git tree")
    prefix = f"inventories/{site}/"
    selected = []
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            raise Blocked("Invalid Fleet Git tree entry")
        path = entry["path"]
        if not path.startswith(prefix):
            continue
        relative = path[len(prefix):]
        if entry.get("type") == "tree" and relative == "group_vars":
            continue
        if (relative == "README.md" and entry.get("type") == "blob"
                and entry.get("mode") == "100644"):
            continue  # Operational documentation is never copied to the guest.
        if (not re.fullmatch(r"(?:[a-z0-9_-]+\.ya?ml|group_vars/[a-z0-9_-]+\.ya?ml)", relative)
                or entry.get("type") != "blob"
                or entry.get("mode") != "100644"
                or not re.fullmatch(r"[0-9a-f]{40}", entry.get("sha") or "")
                or not isinstance(entry.get("size"), int)
                or not 0 < entry["size"] <= 524288):
            raise Blocked("Unexpected content in site inventory namespace: " + path)
        selected.append(path)
    required = {prefix + suffix for suffix in
                ("hosts.yml", "runner.yml", "group_vars/all.yml")}
    if not required.issubset(selected) or len(selected) > 64:
        raise Blocked("Site inventory missing mandatory files or exceeds file count")
    return tuple(sorted(selected))


def verify_site_identity(checkout, site):
    """Fail closed on owner-type mismatch before any PVE guest changes.

    Bootstrap is stdlib-only on the PVE host, so read only the three simple,
    scalar inventory identifiers; Ansible later loads the complete YAML.
    """
    if not re.fullmatch(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)+", site):
        raise Blocked("Site ID must be <owner>-<type>")
    filename = (checkout.parent / "fleet-detail" / "inventories" /
                site / "group_vars" / "all.yml")
    if filename.is_symlink() or not filename.is_file():
        raise Blocked("Canonical site metadata missing or linked")
    try:
        body = filename.read_text(encoding="utf-8")
    except (UnicodeError, OSError):
        raise Blocked("Canonical site metadata unreadable") from None
    if len(body) > 16384:
        raise Blocked("Canonical metadata too large")
    fields = {}
    in_site = False
    site_sections = 0
    for line in body.splitlines():
        if line == "site:":
            in_site = True
            site_sections += 1
            continue
        if line and not line[0].isspace() and not line.startswith("#") and line != "---":
            in_site = False
        if not in_site:
            continue
        match = re.fullmatch(r"  (id|owner_slug|type): ([a-z][a-z0-9-]*)", line)
        if match:
            key, value = match.groups()
            if key in fields:
                raise Blocked("Duplicate canonical site identity field")
            fields[key] = value
    if site_sections != 1:
        raise Blocked("Invalid canonical metadata envelope")
    if set(fields) != {"id", "owner_slug", "type"}:
        raise Blocked("Missing canonical site identity field")
    owner, kind = fields["owner_slug"], fields["type"]
    if (not re.fullmatch(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*", owner)
            or not re.fullmatch(r"[a-z][a-z0-9]*", kind)
            or fields["id"] != site
            or site != owner + "-" + kind):
        raise Blocked("Canonical site identity mismatch")
    print("CANONICAL_SITE_ID=PASS " + site, flush=True)



def fetch_site_inventory(checkout, fleet_sha, env, site):
    """Fetch only one site's inventory; owner OAuth stays on PVE."""
    raw = run("gh", "api",
              f"repos/{FLEET_REPO}/git/trees/{fleet_sha}?recursive=1",
              timeout=60, env=env)
    try:
        data = json.loads(raw)
    except ValueError:
        raise Blocked("Invalid pinned Fleet tree response") from None
    if data.get("truncated") is not False:
        raise Blocked("Pinned Fleet tree truncated; refusing partial site import")
    paths = validate_site_inventory_tree(data.get("tree"), site)
    fleet = checkout.parent / "fleet-detail"
    for path in paths:
        fetch_immutable_file(fleet, fleet_sha, path, env, repository=FLEET_REPO)
    print(f"SITE_INVENTORY_FETCH=PASS site={site} files={len(paths)}", flush=True)
    return paths


def install_site_inventory(site, paths, checkout):
    """Materialize site files root:semaphore only; never push Fleet Vault."""
    prefix = f"inventories/{site}/"
    if not paths or any(not p.startswith(prefix) for p in paths):
        raise Blocked("Untrusted site inventory selection")
    site_root = f"{SITE_INVENTORY_BASE}/{site}"
    print("SITE_INVENTORY_STAGE=START", flush=True)
    for folder in (SITE_INVENTORY_BASE, site_root, site_root + "/group_vars"):
        print("SITE_INVENTORY_DIR_CREATE=" + folder, flush=True)
        if code("pct", "exec", str(CTID), "--", "test", "-L", folder) == 0:
            raise Blocked("Site inventory directory is a symlink")
        pct_guest("install", "-d", "-o", "root", "-g", "semaphore", "-m", "0750",
                  folder)
    fleet = checkout.parent / "fleet-detail"
    for index, path in enumerate(paths):
        print(f"SITE_INVENTORY_FILE_STAGE={index + 1}/{len(paths)}", flush=True)
        relative = path[len(prefix):]
        source = fleet / path
        if source.is_symlink() or not source.is_file():
            raise Blocked("Pinned site file missing or symlinked")
        target = site_root + "/" + relative
        expected_digest = hashlib.sha256(source.read_bytes()).hexdigest()
        if code("pct", "exec", str(CTID), "--", "test", "-e", target) == 0:
            if code("pct", "exec", str(CTID), "--", "test", "-L", target) == 0:
                raise Blocked("Existing site inventory file is a symlink")
            observed = pct_guest("sha256sum", target).split()[0]
            if observed != expected_digest:
                raise Blocked("Existing site inventory differs; controlled refresh required")
        else:
            staging = f"/root/dh-site-upload-{index:03d}"
            if code("pct", "exec", str(CTID), "--", "test", "-e", staging) == 0:
                raise Blocked("Untrusted interrupted inventory upload")
            run("pct", "push", str(CTID), str(source), staging, timeout=30)
            pct_guest("install", "-o", "root", "-g", "semaphore",
                      "-m", "0640", staging, target)
            pct_guest("rm", "-f", staging)
            if pct_guest("sha256sum", target).split()[0] != expected_digest:
                raise Blocked("Guest site file checksum mismatch")
        if pct_guest("stat", "-c", "%a:%U:%G", target) != "640:root:semaphore":
            raise Blocked("Guest inventory permissions mismatch")
    for forbidden in ("/var/lib/ansimble/fleet-detail",
                      "/root/dh-fleet-deploy-key",
                      site_root + "/secrets/fleet.vault.yml"):
        if code("pct", "exec", str(CTID), "--", "test", "-e", forbidden) == 0:
            raise Blocked("Guest contains forbidden fleet credential artifacts")
    print(f"SITE_INVENTORY_GUEST=PASS site={site} files={len(paths)}", flush=True)



def verify_site_escrow(checkout, site):
    """Validate PVE-held escrow required by this site's pinned PRIVATE runner data.

    No customer name, path or credential is embedded in the shared bootstrap.
    This is a strict subset of runner.yml, parsed with the Python stdlib on PVE.
    """
    runner = checkout.parent / "fleet-detail" / "inventories" / site / "runner.yml"
    if runner.is_symlink() or not runner.is_file():
        raise Blocked("Pinned Fleet runner inventory missing")
    if runner.stat().st_size > 65536:
        raise Blocked("Fleet runner inventory too large")
    lines = runner.read_text(encoding="utf-8").splitlines()
    in_bootstrap = False
    in_escrow = False
    seen = False
    paths = []
    for line in lines:
        if line == "  bootstrap:":
            if in_bootstrap:
                raise Blocked("Duplicate Fleet bootstrap stanza")
            in_bootstrap, in_escrow = True, False
            continue
        if in_bootstrap and line and not line.startswith((" ", "#")):
            in_bootstrap, in_escrow = False, False
        if in_bootstrap and line.startswith("  ") and not line.startswith("    ") and line != "  bootstrap:":
            in_bootstrap, in_escrow = False, False
        if not in_bootstrap:
            continue
        if line == "    pve_escrow_files:":
            if seen:
                raise Blocked("Duplicate site escrow declaration")
            seen, in_escrow = True, True
            continue
        if in_escrow:
            if line.startswith("      - "):
                name = line[8:]
                if (not re.fullmatch(r"[a-zA-Z0-9_./-]+", name)
                        or any(part in ("", ".", "..") for part in name.split("/"))
                        or name.startswith("/")):
                    raise Blocked("Unsafe relative site escrow path")
                paths.append(name)
                continue
            if line.strip() and not line.lstrip().startswith("#"):
                in_escrow = False
    if seen and (not paths or len(paths) > 16 or len(set(paths)) != len(paths)):
        raise Blocked("Invalid site escrow file declarations")
    for name in paths:
        candidate = ROOT_STAGE / name
        relatives = candidate.relative_to(ROOT_STAGE).parts
        parent = ROOT_STAGE
        if parent.is_symlink() or not parent.is_dir() or parent.stat().st_uid != 0:
            raise Blocked("Site escrow root is not trusted")
        for part in relatives[:-1]:
            parent = parent / part
            if parent.is_symlink() or not parent.is_dir() or parent.stat().st_uid != 0:
                raise Blocked("Site escrow directory not trusted")
        if (candidate.is_symlink() or not candidate.is_file()
                or candidate.stat().st_uid != 0
                or candidate.stat().st_mode & 0o077):
            raise Blocked("Site escrow artifact missing or unsafe")
    print(f"SITE_ESCROW_GATE=PASS count={len(paths)}", flush=True)


def validate_fleet_password(checkout):
    path = checkout / "bootstrap/fleet-master-gate.py"
    spec = importlib.util.spec_from_file_location("dh_master_gate", path)
    if spec is None or spec.loader is None:
        raise Blocked("Vault gate unavailable")
    gate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gate)
    vault = checkout.parent / "fleet-detail" / "secrets/fleet.vault.yml"
    digest = hashlib.sha256(vault.read_bytes()).hexdigest()
    ciphertext = gate.read_pinned_vault(vault, digest)
    password = getpass.getpass("OWNER: Fleet master password (hidden): ")
    authenticated = gate.password_authenticates(ciphertext, password.encode("utf-8"))
    if not authenticated:
        raise Blocked("Fleet master password rejected; PVE unchanged")
    print("FLEET_MASTER_PASSWORD=PASS (not stored)", flush=True)
    print("FULL_VAULT_DECRYPTION=PENDING_AFTER_ANSIBLE", flush=True)
    return password


def pct_guest(*argv, timeout=180):
    return run("pct", "exec", str(CTID), "--", *argv, timeout=timeout)



def ensure_debian_etc_before_first_boot():
    """Repair the Debian template /etc mode while CT1231 is still stopped.

    An unprivileged dbus-daemon cannot traverse a 0700 /etc. Never start systemd
    before correcting it. Mount only the known CT rootfs, then always unmount.
    """
    if run("pct", "status", str(CTID)) != "status: stopped":
        raise Blocked("Offline /etc repair requires stopped CT1231")
    mounted = False
    try:
        run("pct", "mount", str(CTID), timeout=60)
        mounted = True
        rootfs = Path(f"/var/lib/lxc/{CTID}/rootfs")
        etc = rootfs / "etc"
        if rootfs.is_symlink() or etc.is_symlink() or not etc.is_dir():
            raise Blocked("Unexpected mounted rootfs /etc; refusing offline repair")
        root_stat = rootfs.stat()
        before = etc.stat()
        if (before.st_uid != root_stat.st_uid
                or before.st_gid != root_stat.st_gid):
            raise Blocked("Mounted guest /etc owner differs from rootfs")
        mode = before.st_mode & 0o7777
        if mode == 0o700:
            etc.chmod(0o755)
            print("GUEST_ETC_PREBOOT=REPAIRED 0700->0755", flush=True)
        elif mode == 0o755:
            print("GUEST_ETC_PREBOOT=PASS 0755", flush=True)
        else:
            raise Blocked("Unexpected mounted guest /etc mode; manual audit required")
        after = etc.stat()
        if (after.st_mode & 0o7777 != 0o755
                or after.st_uid != before.st_uid
                or after.st_gid != before.st_gid):
            raise Blocked("Guest /etc offline verification failed")
    finally:
        if mounted:
            run("pct", "unmount", str(CTID), timeout=60)
    print("GUEST_ETC_PREBOOT=VERIFIED", flush=True)


def verify_fresh_system_bus(*, attempts=30, delay_seconds=2):
    """Wait for first-boot systemd and D-Bus, without masking persistent failure."""
    probes = (
        ("dbus.service", ("systemctl", "is-active", "dbus.service")),
        ("dbus.socket", ("systemctl", "is-active", "dbus.socket")),
        ("systemd", ("systemctl", "is-system-running")),
    )
    last = []
    for attempt in range(attempts):
        last = [(name, code("pct", "exec", str(CTID), "--", *command))
                for name, command in probes]
        if all(rc == 0 for _, rc in last):
            print("FRESH_DEBIAN_DBUS=PASS", flush=True)
            return
        if attempt + 1 < attempts:
            time.sleep(delay_seconds)
    print("FRESH_DEBIAN_DBUS=FAILED " +
          " ".join(f"{name}_rc={rc}" for name, rc in last), flush=True)
    raise Blocked("First-boot systemd/D-Bus readiness timeout; manual audit required")


def create_runner(site, cidr, gateway, template):
    print("CT_CREATE=START", flush=True)
    run("pct", "create", str(CTID), template,
        "--hostname", runner_hostname(site),
        "--ostype", "debian", "--cores", "1", "--memory", "1024",
        "--swap", "512", "--rootfs", "local-lvm:16",
        "--unprivileged", "1", "--features", "nesting=1",
        "--net0", f"name=eth0,bridge={BRIDGE},ip={cidr},gw={gateway}",
        "--onboot", "0", "--protection", "1", "--start", "0", timeout=300)
    config = run("pct", "config", str(CTID))
    if f"hostname: {runner_hostname(site)}" not in config or f"ip={cidr}" not in config:
        raise Blocked("CT config mismatch. Manual recovery required.")
    ensure_debian_etc_before_first_boot()
    run("pct", "start", str(CTID), timeout=90)
    verify_fresh_system_bus()
    if pct_guest("hostname") != runner_hostname(site):
        raise Blocked("Guest hostname wrong")
    print("CT_CREATE=PASS", flush=True)


def ensure_debian_etc_traversable():
    """Correct the observed Debian 13 template /etc=0700 before any APT operation."""
    observed = pct_guest("stat", "-c", "%a:%u:%g:%F", "/etc")
    if observed == "755:0:0:directory":
        print("GUEST_ETC_PERMISSIONS=PASS 0755", flush=True)
        return
    if observed != "700:0:0:directory":
        raise Blocked("Unexpected guest /etc identity or permissions; manual audit required")
    pct_guest("chmod", "0755", "/etc")
    if pct_guest("stat", "-c", "%a:%u:%g:%F", "/etc") != "755:0:0:directory":
        raise Blocked("Guest /etc permissions repair did not verify")
    print("GUEST_ETC_PERMISSIONS=REPAIRED 0700->0755", flush=True)


def install_guest(site, runner_ip, checkout, *, resume_ansible=False):
    ensure_debian_etc_traversable()
    # APT can return 0 on partial index failure; never continue with stale indexes.
    pct_guest("apt-get", "update", "-o", "APT::Update::Error-Mode=any",
              "-o", "Acquire::Retries=2", timeout=240)
    pct_guest("env", "DEBIAN_FRONTEND=noninteractive",
              "apt-get", "install", "-y", "--no-install-recommends",
              "python3", "iproute2", "ca-certificates", timeout=240)
    pct_guest("mkdir", "-p", "/opt/digitalhouses/bootstrap", timeout=20)
    for item in ("semaphore-guest-install.py", "semaphore-project.py"):
        run("pct", "push", str(CTID), str(checkout / "bootstrap" / item),
            "/opt/digitalhouses/bootstrap/" + item, timeout=30)
    run("pct", "push", str(CTID), str(checkout / "requirements/runner.txt"),
        "/opt/digitalhouses/bootstrap/runner.txt", timeout=30)
    guest_args = ["python3", "/opt/digitalhouses/bootstrap/semaphore-guest-install.py",
                  "--site", site, "--runner-ip", runner_ip,
                  "--requirements", "/opt/digitalhouses/bootstrap/runner.txt"]
    if resume_ansible:
        guest_args.append("--resume-ansible")
    guest_args.append("--apply")
    print(pct_guest(*guest_args, timeout=1000), flush=True)
    print("SEMAPHORE_HTTPS_RUNTIME=PASS", flush=True)


def stage_runner_remote_commander(site, checkout):
    """Stage a root-owned fixed-purpose systemd.path handoff; never install automatically.

    Semaphore has NoNewPrivileges=true and cannot use sudo. Its single local
    request triggers ONLY the root-owned pinned Remote Commander playbook.
    """
    if not re.fullmatch(r"[a-z][a-z0-9_-]{0,30}", site):
        raise Blocked("Invalid Remote Commander site")
    if pct_guest("hostname") != runner_hostname(site):
        raise Blocked("Remote Commander runner hostname mismatch")

    base = REMOTE_BOOTSTRAP_DIR
    folders = {base, base + "/playbooks", base + "/roles",
               base + "/roles/runner_remote_commander"}
    for item in REMOTE_INSTALL_FILES:
        folders.add(base + "/" + str(Path(item).parent))
    for folder in sorted(folders, key=lambda path: (path.count("/"), path)):
        pct_guest("install", "-d", "-o", "root", "-g", "root", "-m", "0755", folder)

    for item in REMOTE_INSTALL_FILES:
        source = checkout / item
        if source.is_symlink() or not source.is_file():
            raise Blocked("Pinned Remote Commander source missing: " + item)
        destination = base + "/" + item
        run("pct", "push", str(CTID), str(source), destination, timeout=30)
        pct_guest("chmod", "0644", destination)

    handoff = (
        ("bootstrap/remote-commander-install-request.sh",
         "/usr/local/sbin/dh-remote-commander-install", "0700"),
        ("bootstrap/dh-remote-commander-install.service",
         "/etc/systemd/system/dh-remote-commander-install.service", "0644"),
        ("bootstrap/dh-remote-commander-install.path",
         "/etc/systemd/system/dh-remote-commander-install.path", "0644"),
    )
    for relative, destination, mode in handoff:
        source = checkout / relative
        if source.is_symlink() or not source.is_file():
            raise Blocked("Pinned on-demand artifact missing: " + relative)
        run("pct", "push", str(CTID), str(source), destination, timeout=30)
        pct_guest("chmod", mode, destination)
    pct_guest("systemctl", "daemon-reload")
    pct_guest("systemctl", "enable", "--now", "dh-remote-commander-install.path")
    if pct_guest("systemctl", "is-active", "dh-remote-commander-install.path") != "active":
        raise Blocked("On-demand Remote Commander watcher not active")
    print("REMOTE_COMMANDER_ON_DEMAND=READY (not automatically installed)", flush=True)


def assert_private_fleet_head(expected_fleet_commit: str, env: dict) -> dict:
    """No secret issue until private Git HEAD agrees with the pinned snapshot."""
    target = "repos/" + FLEET_REPO
    current = json.loads(run("gh", "api", target + "/git/ref/heads/main",
                             timeout=25, env=env))
    if current.get("object", {}).get("sha") != expected_fleet_commit:
        raise Blocked("Fleet HEAD differs from pinned release; do not issue tokens")
    record = json.loads(run("gh", "api", target + "/contents/secrets/fleet.vault.yml?ref=main",
                            timeout=25, env=env))
    if record.get("type") != "file" or record.get("encoding") != "base64":
        raise Blocked("Fleet Vault source is not a regular file")
    return record


def publish_encrypted_pve_vault(source_vault: Path, candidate: Path,
                                expected_fleet_commit: str, env: dict) -> str:
    """Publish ONLY encrypted Vault ciphertext using GitHub Contents CAS."""
    current = assert_private_fleet_head(expected_fleet_commit, env)
    current_bytes = base64.b64decode(current["content"])
    if current_bytes != source_vault.read_bytes():
        raise Blocked("Pinned Fleet Vault content differs from GitHub main")
    proposed = candidate.read_bytes()
    if not proposed.startswith(b"$ANSIBLE_VAULT;") or len(proposed) > 4 * 1024 * 1024:
        raise Blocked("Invalid encrypted Fleet Vault candidate")
    payload = {
        "message": "Fleet: store canonical PVE site access credentials (encrypted)",
        "content": base64.b64encode(proposed).decode("ascii"),
        "sha": current["sha"],
        "branch": "main",
    }
    with tempfile.TemporaryDirectory(prefix="dh-fleet-publication-", dir="/run") as loc:
        request_path = Path(loc) / "request.json"
        request_path.write_text(json.dumps(payload), encoding="utf-8")
        request_path.chmod(0o600)
        response = json.loads(run(
            "gh", "api", "--method", "PUT",
            "repos/" + FLEET_REPO + "/contents/secrets/fleet.vault.yml",
            "--input", str(request_path), timeout=60, env=env,
        ))
        commit_sha = response.get("commit", {}).get("sha")
        if not isinstance(commit_sha, str) or not re.fullmatch(r"[a-f0-9]{40}", commit_sha):
            raise Blocked("GitHub Vault write uncertain; reconcile read-only, no retry")
    # Never retry the write when a network/API result is ambiguous.
    record = json.loads(run(
        "gh", "api", "repos/" + FLEET_REPO + "/contents/secrets/fleet.vault.yml?ref=" + commit_sha,
        timeout=30, env=env))
    if base64.b64decode(record.get("content", "")) != proposed:
        raise Blocked("Committed encrypted Vault verification failed")
    print("FLEET_VAULT_ENCRYPTED_PUBLICATION=PASS", flush=True)
    print("FLEET_NEW_COMMIT=" + commit_sha, flush=True)
    return commit_sha


def provision_pve_identity_stage(site: str, runner_cidr: str, pve_ip: str,
                                 checkout: Path, expected_fleet_commit: str,
                                 owner_master: str, env: dict) -> None:
    """Create credentials as PVE root; seal to Fleet Vault before declaring PASS.

    Fail closed on concurrent Fleet changes; no secret values in stdout or argv.
    """
    record = assert_private_fleet_head(expected_fleet_commit, env)
    source_vault = checkout.parent / "fleet-detail/secrets/fleet.vault.yml"
    if base64.b64decode(record.get("content", "")) != source_vault.read_bytes():
        raise Blocked("Fleet source is not pinned to the current private main")
    source = checkout / "bootstrap/pve-identities.py"
    worker = checkout / "bootstrap/import-pve-identities-vault.py"
    if any(path.is_symlink() or not path.is_file() for path in (source, worker)):
        raise Blocked("Pinned PVE identity bootstrap or Vault worker missing")
    runner_ip = str(ipaddress.IPv4Interface(runner_cidr).ip)
    # PVE Linux user/tokens have not been created before all gates above.
    run("python3", str(source),
        "--site", site, "--runner-ctid", str(CTID),
        "--runner-ip", runner_ip, "--pve-ip", pve_ip,
        "--apply", timeout=180)
    print("PVE_CANONICAL_IDENTITIES=ROOT_STAGE_PASS", flush=True)

    # Required data remain root-only on PVE, and all guest secrets are temporary.
    pct_guest("install", "-d", "-m", "0700", "/opt/digitalhouses/bootstrap")
    run("pct", "push", str(CTID), str(worker),
        "/opt/digitalhouses/bootstrap/import-pve-identities-vault.py")
    pct_guest("chmod", "0700", "/opt/digitalhouses/bootstrap/import-pve-identities-vault.py")
    with tempfile.TemporaryDirectory(prefix="dh-pve-vault-", dir="/run") as loc:
        tmp = Path(loc)
        master_path = tmp / "master"
        master_path.write_bytes(owner_master.encode("utf-8") + b"\\n")
        master_path.chmod(0o600)
        candidate = tmp / "candidate.vault.yml"
        run("python3", str(worker),
            "--site", site, "--ctid", str(CTID),
            "--runner-ip", runner_ip, "--pve-ip", pve_ip,
            "--master-file", str(master_path),
            "--vault-file", str(source_vault),
            "--output-file", str(candidate), timeout=150)
        publish_encrypted_pve_vault(source_vault, candidate,
                                    expected_fleet_commit, env)
    print("PVE_CREDENTIALS=ENCRYPTED_FLEET_PUBLISHED", flush=True)


def resolve_project_title(checkout: Path, site: str, provided: str | None) -> str:
    """Site names are inventory data rather than special-cased code."""
    if provided:
        return provided
    source = checkout.parent / "fleet-detail" / "inventories" / site / "runner.yml"
    for line in source.read_text(encoding="utf-8").splitlines():
        if line.startswith("  site_name: "):
            value = line.partition(": ")[2].strip().strip('"')
            if 1 <= len(value) <= 100:
                return value
    return site

def seed_semaphore(site, project_title):
    pct_guest("python3", "/opt/digitalhouses/bootstrap/semaphore-project.py",
              "--site", site, "--project-title", project_title,
              "--admin-password-file", "/root/dh-semaphore-initial-admin-password",
              "--apply", timeout=120)
    print("SEMAPHORE_TEMPLATES=PASS (no task executed)", flush=True)


def apply(args, template):
    if not re.fullmatch(r"[a-f0-9]{40}", args.expected_commit or ""):
        raise Blocked("Immutable 40-character code release SHA required")
    if not re.fullmatch(r"[a-f0-9]{40}", args.expected_fleet_commit or ""):
        raise Blocked("Immutable 40-character Fleet Detail SHA required")
    with tempfile.TemporaryDirectory(prefix="dh-semaphore-bootstrap-") as location:
        work = Path(location)
        if args.use_github_session:
            candidate = os.environ.get("GH_CONFIG_DIR", "")
            config = Path(candidate)
            if not config.is_absolute() or config.is_symlink() or not config.is_dir():
                raise Blocked("Trusted launcher GitHub config directory missing")
            info = config.stat()
            if info.st_uid != 0 or info.st_mode & 0o077:
                raise Blocked("Unsafe temporary GitHub authorization directory")
        else:
            config = work / "gh"
            config.mkdir(mode=0o700)
        gh_binary = ensure_ephemeral_gh(work)
        env = git_env(config)
        env["PATH"] = str(gh_binary.parent) + os.pathsep + env.get("PATH", "")
        checkout = work / "source"
        site_files = authenticate_git(checkout, args.expected_commit,
                                      args.expected_fleet_commit, env, args.site,
                                      preauthorized=args.use_github_session)
        owner_master = validate_fleet_password(checkout)
        verify_site_escrow(checkout, args.site)
        # Site ID y/n is confirmed by the public installer before auth.
        # Resume operations still require an explicit extra confirmation.
        if args.resume_semaphore:
            print(f"PLAN: finish verified running Semaphore in CT{CTID} {runner_hostname(args.site)} at {args.runner_cidr}", flush=True)
            confirm = input(f"Type COMPLETE SEMAPHORE CT{CTID} to continue: ")
            if confirm != f"COMPLETE SEMAPHORE CT{CTID}":
                raise Blocked("Operator cancelled; existing guest unchanged")
        elif args.resume_fresh:
            print(f"PLAN: continue verified freshly booted empty CT{CTID} at {args.runner_cidr}", flush=True)
            confirm = input(f"Type RESUME FRESH CT{CTID} to continue: ")
            if confirm != f"RESUME FRESH CT{CTID}":
                raise Blocked("Fresh runner resume cancelled; guest unchanged")
        elif args.resume_ansible:
            print(f"PLAN: continue existing Ansible-only CT{CTID} {runner_hostname(args.site)} at {args.runner_cidr}", flush=True)
            confirm = input(f"Type RESUME ANSIBLE CT{CTID} to continue: ")
            if confirm != f"RESUME ANSIBLE CT{CTID}":
                raise Blocked("Operator cancelled; existing guest unchanged")
        elif args.resume:
            print(f"PLAN: resume verified existing CT{CTID} {runner_hostname(args.site)} at {args.runner_cidr}", flush=True)
            confirm = input(f"Type RESUME CT{CTID} to continue: ")
            if confirm != f"RESUME CT{CTID}":
                raise Blocked("Operator cancelled; existing guest unchanged")
        else:
            # Fresh creation is authorized by the already confirmed Site ID,
            # validated private Fleet metadata, Vault/escrow and strict PVE preflight.
            # Never request a second magic phrase for creating an absent CT.
            print(f"PLAN: create CT{CTID} {runner_hostname(args.site)} at {args.runner_cidr}", flush=True)
            print("CT_CREATE_CONFIRMATION=SITE_ID_ALREADY_CONFIRMED", flush=True)
        host_preflight(args.site, args.runner_cidr, args.gateway, args.pve_ip,
                       resume=args.resume, resume_ansible=args.resume_ansible,
                       resume_semaphore=args.resume_semaphore, resume_fresh=args.resume_fresh)
        if not args.resume_semaphore:
            if not (args.resume or args.resume_ansible or args.resume_fresh):
                template = ensure_official_debian_template()
                create_runner(args.site, args.runner_cidr, args.gateway, template)
            install_guest(args.site, str(ipaddress.IPv4Interface(args.runner_cidr).ip),
                          checkout, resume_ansible=args.resume_ansible)
        install_site_inventory(args.site, site_files, checkout)
        project_title = resolve_project_title(checkout, args.site, args.project_title)
        if args.resume_semaphore:
            # Restore only the immutable API seeder; never alter the DB directly.
            run("pct", "push", str(CTID),
                str(checkout / "bootstrap/semaphore-project.py"),
                "/opt/digitalhouses/bootstrap/semaphore-project.py")
            probe = pct_guest("python3",
                              "/opt/digitalhouses/bootstrap/semaphore-project.py",
                              "--site", args.site, "--project-title", project_title,
                              "--admin-password-file",
                              "/root/dh-semaphore-initial-admin-password", timeout=60)
            if "NO_INFRASTRUCTURE_TASKS_LAUNCHED=YES" not in probe:
                raise Blocked("Semaphore owner login/API audit not confirmed")
            print("SEMAPHORE_ADMIN_API_AUTH=PASS", flush=True)
        seed_semaphore(args.site, project_title)
        stage_runner_remote_commander(args.site, checkout)
        # Credential enrollment is part of a NEW bootstrap only; recovery
        # and resume cannot silently issue API users/tokens or SSH keys.
        if not (args.resume or args.resume_fresh or args.resume_ansible
                or args.resume_semaphore):
            provision_pve_identity_stage(args.site, args.runner_cidr,
                                         args.pve_ip, checkout,
                                         args.expected_fleet_commit, owner_master, env)
    print(f"DONE: Semaphore https://{ipaddress.IPv4Interface(args.runner_cidr).ip}:3000/")
    print("ADMIN_PASSWORD=ROOT_ONLY_GUEST_FILE (never post to chat)")
    print("FLEET_VAULT_ON_GUEST=NO")
    print("FLEET_GITHUB_KEY_ON_GUEST=NO")
    print("SITE_RUNTIME_SECRET_ISSUANCE=NOT_YET_ACCEPTED")
    print("PVE_WRITE_OPERATIONS=NOT_ENABLED")
    print("GH_OAUTH_CONFIG=CLEANED_LOCALLY (revoke on GitHub if required)")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site", required=True)
    parser.add_argument("--runner-cidr", required=True)
    parser.add_argument("--gateway", required=True)
    parser.add_argument("--pve-ip", required=True)
    parser.add_argument("--project-title")
    parser.add_argument("--expected-commit")
    parser.add_argument("--expected-fleet-commit")
    parser.add_argument("--use-github-session", action="store_true")
    resume_group = parser.add_mutually_exclusive_group()
    resume_group.add_argument("--resume-fresh", action="store_true",
                              help="Continue verified freshly booted empty CT1231")
    resume_group.add_argument("--resume", action="store_true",
                              help="Resume verified empty CT1231; never create a guest")
    resume_group.add_argument("--resume-ansible", action="store_true",
                              help="Resume verified Ansible-only CT1231; never create a guest")
    resume_group.add_argument("--resume-semaphore", action="store_true",
                              help="Complete verified running Semaphore; seed project only")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    template = host_preflight(args.site, args.runner_cidr, args.gateway, args.pve_ip,
                              resume=args.resume, resume_ansible=args.resume_ansible,
                              resume_semaphore=args.resume_semaphore, resume_fresh=args.resume_fresh)
    if not args.apply:
        print("MODE=READ_ONLY; GH/Vault authentication and guest writes not executed")
        return 0
    if not args.expected_commit or not args.expected_fleet_commit:
        raise Blocked("Both pinned Git commits are required for --apply")
    apply(args, template)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Blocked as err:
        print(f"BLOCKED: {err}", file=sys.stderr)
        sys.exit(2)
