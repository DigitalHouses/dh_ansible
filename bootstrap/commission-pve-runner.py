#!/usr/bin/env python3
"""One-time PVE-host-root commissioning for a site-local Ansible runner.

Run on the actual Proxmox VE host, never inside the runner. Creates a
site-local SSH public-key trust between the already-running Semaphore
account and the PVE host. Does not create/delete VMs, modify disks, or
touch other guests. Re-runs are idempotent.
"""
from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import shlex
import socket
import ssl
import stat
import subprocess
import sys


class Blocked(RuntimeError):
    pass


def run(*args, input_text=None, timeout=40, check=True):
    result = subprocess.run(
        args, input=input_text, text=True, capture_output=True,
        timeout=timeout, check=False,
    )
    if check and result.returncode:
        raise Blocked(f"COMMAND_FAILED: {args[0]} rc={result.returncode}: "
                      f"{result.stderr.strip()[:350]}")
    return result


def pct(ctid, *args, input_text=None):
    return run("pct", "exec", str(ctid), "--", *args, input_text=input_text)


def require(condition, message):
    if not condition:
        raise Blocked(message)


def guest_file(ctid, path, value, owner, mode):
    pct(ctid, "tee", path, input_text=value)
    pct(ctid, "chown", owner, path)
    pct(ctid, "chmod", mode, path)


def observed_host():
    require(os.geteuid() == 0 and Path("/etc/pve/user.cfg").is_file(),
            "PVE_HOST_ROOT_REQUIRED")
    addr = json.loads(run("ip", "-j", "-4", "addr", "show",
                          "dev", "vmbr0").stdout)
    require(len(addr) == 1, "PVE_VMBR0_NOT_UNIQUE")
    global_ip = [a for a in addr[0].get("addr_info", [])
                 if a.get("family") == "inet"
                 and a.get("scope") == "global"]
    require(len(global_ip) == 1, "PVE_VMBR0_IP_NOT_UNIQUE")
    iface = ipaddress.IPv4Interface(
        f"{global_ip[0]['local']}/{global_ip[0]['prefixlen']}"
    )
    require(not iface.ip.is_loopback and not iface.ip.is_link_local,
            "PVE_IP_INVALID")
    node = run("hostname", "-s").stdout.strip()
    require(re.fullmatch(r"[a-zA-Z][a-zA-Z0-9-]{0,62}", node),
            "PVE_NODE_INVALID")
    return iface, node


def read_live_certificate(ip):
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    with socket.create_connection((str(ip), 8006), timeout=7) as sock:
        with context.wrap_socket(sock, server_hostname=str(ip)) as tls:
            der = tls.getpeercert(binary_form=True)
    require(bool(der), "PVE_TLS_CERT_MISSING")
    raw = hashlib.sha256(der).hexdigest().upper()
    return ":".join(raw[i:i + 2] for i in range(0, 64, 2))


def runner_identity(ctid, iface):
    result = pct(ctid, "hostname").stdout.strip()
    require(re.fullmatch(r"ansible-[a-z][a-z0-9]*(-[a-z0-9]+)+", result),
            "RUNNER_HOSTNAME_INVALID")
    runner = json.loads(pct(ctid, "ip", "-j", "-4", "addr", "show",
                            "dev", "eth0").stdout)
    require(len(runner) == 1, "RUNNER_IP_INVALID")
    active = [a for a in runner[0].get("addr_info", [])
              if a.get("family") == "inet" and a.get("scope") == "global"]
    require(len(active) == 1, "RUNNER_IP_NOT_UNIQUE")
    runner_ip = ipaddress.IPv4Address(active[0]["local"])
    require(runner_ip in iface.network and runner_ip != iface.ip,
            "RUNNER_WRONG_PVE_SUBNET")
    require(int(str(runner_ip).split(".")[-1]) == 231,
            "RUNNER_NONSTANDARD_IP")
    require(run("pct", "status", str(ctid)).stdout.strip().endswith("running"),
            "RUNNER_NOT_RUNNING")
    return result.removeprefix("ansible-"), str(runner_ip)


def ensure_guest_key(ctid, site):
    folder = "/var/lib/semaphore/.ssh"
    key = folder + "/dh_pve_admin_ed25519"
    pub = key + ".pub"
    pct(ctid, "install", "-d", "-o", "semaphore", "-g", "semaphore",
        "-m", "0700", folder)
    private_stat = run("pct", "exec", str(ctid), "--", "test", "-f",
                       key, check=False).returncode == 0
    public_stat = run("pct", "exec", str(ctid), "--", "test", "-f",
                      pub, check=False).returncode == 0
    require(private_stat == public_stat, "PVE_RUNNER_SSH_PARTIAL_KEYPAIR")
    if not private_stat:
        pct(ctid, "ssh-keygen", "-q", "-t", "ed25519",
            "-N", "", "-C", "dh-pve-" + site, "-f", key)
    for file, mode in ((key, "0600"), (pub, "0644")):
        pct(ctid, "chown", "semaphore:semaphore", file)
        pct(ctid, "chmod", mode, file)
    public = pct(ctid, "cat", pub).stdout.strip()
    parts = public.split()
    require(len(parts) >= 2 and parts[0] == "ssh-ed25519"
            and len(parts[1]) >= 48, "PVE_RUNNER_SSH_PUBKEY_INVALID")
    return key, public


def authorize_host_key(public):
    folder = Path("/root/.ssh")
    require(not folder.is_symlink(), "PVE_ROOT_SSH_DIR_SYMLINK")
    folder.mkdir(mode=0o700, exist_ok=True)
    require(folder.is_dir() and folder.stat().st_uid == 0,
            "PVE_ROOT_SSH_DIR_INVALID")
    folder.chmod(0o700)
    dest = folder / "authorized_keys"
    if dest.is_symlink():
        resolved = dest.resolve(strict=True)
        require(resolved == Path("/etc/pve/priv/authorized_keys"),
                "PVE_ROOT_AUTH_KEYS_UNEXPECTED_LINK")
    elif dest.exists():
        require(dest.is_file() and dest.stat().st_uid == 0,
                "PVE_ROOT_AUTH_KEYS_UNSAFE")
    token = public.split()[1]
    existing = dest.read_text() if dest.exists() else ""
    present = any(len(parts := line.split()) >= 2
                  and parts[0] == "ssh-ed25519" and parts[1] == token
                  for line in existing.splitlines())
    if not present:
        with dest.open("a", encoding="utf-8") as file:
            if existing and not existing.endswith("\n"):
                file.write("\n")
            file.write(public + "\n")
        if not dest.is_symlink():
            dest.chmod(0o600)
    return not present


def install_guest_trust(ctid, ip, node, fingerprint):
    host_pub = Path("/etc/ssh/ssh_host_ed25519_key.pub")
    require(host_pub.is_file() and not host_pub.is_symlink(),
            "PVE_SSH_ED25519_HOST_KEY_MISSING")
    parts = host_pub.read_text().split()
    require(len(parts) >= 2 and parts[0] == "ssh-ed25519",
            "PVE_SSH_HOST_KEY_INVALID")
    known = "/var/lib/semaphore/.ssh/dh_pve_known_hosts"
    guest_file(ctid, known, f"{ip} ssh-ed25519 {parts[1]}\n",
               "semaphore:semaphore", "0600")

    site = pct(ctid, "hostname").stdout.strip().removeprefix("ansible-")
    base = "/var/lib/ansible/site-data/" + site
    observed = (
        "---\npve_observed:\n"
        f"  ipv4: {ip}\n"
        "  source: host_root_vmbr0\n"
        f"  node: {node}\n"
        f"  tls_sha256_fingerprint: '{fingerprint}'\n"
    )
    guest_file(ctid, base + "/pve-runtime.yml", observed,
               "root:semaphore", "0640")
    return known


def hardware_report(ctid, site, ip, node, fingerprint):
    # Sanitized hardware metadata, not secrets, passwords or storage changes.
    results = {}
    commands = {
        "disks": ("lsblk", "-J", "-b", "-o",
                  "NAME,PATH,TYPE,SIZE,MODEL,SERIAL,WWN,TRAN,MOUNTPOINTS"),
        "pci": ("lspci", "-Dnn"),
        "vms": ("qm", "list"),
        "storage": ("pvesm", "status"),
        "zfs": ("zpool", "list", "-H", "-o", "name,guid,size,health"),
    }
    for key, argv in commands.items():
        p = run(*argv, check=False, timeout=35)
        results[key] = (json.loads(p.stdout) if key == "disks" and
                        p.returncode == 0 else
                        (p.stdout if p.returncode == 0 else
                         "UNAVAILABLE: rc=" + str(p.returncode)))
    report = json.dumps({
        "schema": 1, "site": site, "pve_ipv4": ip, "pve_node": node,
        "pve_tls_sha256_fingerprint": fingerprint,
        "observations": results,
    }, indent=2, ensure_ascii=False) + "\n"
    base = "/var/lib/ansible/site-data/" + site
    guest_file(ctid, base + "/pve-hardware-observed.json", report,
               "root:semaphore", "0640")


def verify_ssh(ctid, key, known, ip):
    result = pct(
        ctid, "runuser", "-u", "semaphore", "--",
        "ssh", "-T", "-o", "BatchMode=yes",
        "-o", "IdentitiesOnly=yes",
        "-o", "StrictHostKeyChecking=yes",
        "-o", "ConnectTimeout=7",
        "-o", f"UserKnownHostsFile={known}",
        "-i", key, f"root@{ip}", "/usr/bin/id", "-u",
    )
    require(result.stdout.strip() == "0", "PVE_SSH_ROOT_LOGIN_NOT_READY")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--runner-ctid", type=int, default=1231)
    args = parser.parse_args()
    require(100 <= args.runner_ctid <= 999999, "CTID_INVALID")
    iface, node = observed_host()
    site, runner_ip = runner_identity(args.runner_ctid, iface)
    fingerprint = read_live_certificate(iface.ip)
    key, public = ensure_guest_key(args.runner_ctid, site)
    authorize_host_key(public)
    known = install_guest_trust(args.runner_ctid, iface.ip, node, fingerprint)
    verify_ssh(args.runner_ctid, key, known, iface.ip)
    hardware_report(args.runner_ctid, site, str(iface.ip), node, fingerprint)
    print("PVE_RUNNER_SITE=" + site)
    print("PVE_RUNNER_IPV4=" + runner_ip)
    print("PVE_ACTUAL_IPV4=" + str(iface.ip))
    print("PVE_NODE=" + node)
    print("PVE_TLS_FINGERPRINT=" + fingerprint)
    print("PVE_NATIVE_SSH=PASS")
    print("PVE_HARDWARE_AUDIT=PASS")
    print("PVE_COMMISSIONING=PASS")


if __name__ == "__main__":
    try:
        main()
    except (Blocked, OSError, subprocess.TimeoutExpired,
            ValueError, json.JSONDecodeError) as error:
        print(f"PVE_COMMISSIONING=BLOCKED: {error}", file=sys.stderr)
        sys.exit(2)
