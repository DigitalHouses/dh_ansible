#!/usr/bin/env python3
"""PVE fixed-purpose Debian 13 media VM provisioning (one canonical site VM).

Called from the existing Semaphore PVE root worker. No VM deletion, storage
formatting, legacy guest mutation, or host controller rebinding is performed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import urllib.request

import yaml

IMAGE_URL = "https://cloud.debian.org/images/cloud/trixie/latest/"
IMAGE_NAME = "debian-13-generic-amd64.qcow2"
CACHE = Path("/var/cache/digitalhouses/media")
RUNNER = "1231"
SITE_PATTERN = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)+\Z")
BDF_PATTERN = re.compile(r"[0-9a-f]{4}:[0-9a-f]{2}:[0-9a-f]{2}\\.[0-7]\Z")


class Blocked(Exception):
    pass


def need(condition, message):
    if not condition:
        raise Blocked(message)


def run(*args, timeout=90):
    result = subprocess.run(list(args), text=True, capture_output=True,
                            timeout=timeout, check=False)
    if result.returncode:
        raise Blocked("COMMAND_FAILED: " + Path(args[0]).name
                      + " rc=" + str(result.returncode))
    return result.stdout.strip()


def qm(*args, timeout=90):
    return run("/usr/sbin/qm", *map(str, args), timeout=timeout)


def pct(*args):
    return run("/usr/sbin/pct", "exec", RUNNER, "--", *args)


def config(vmid):
    return dict(line.split(": ", 1) for line in qm("config", vmid).splitlines()
                if ": " in line)


def site_data(site):
    need(os.geteuid() == 0 and Path("/etc/pve/user.cfg").is_file(),
         "PVE_ROOT_REQUIRED")
    need(SITE_PATTERN.fullmatch(site) is not None, "SITE_INVALID")
    need(qm_version() is not None, "PVE_QEMU_REQUIRED")
    need(pct("hostname") == "ansible-" + site, "RUNNER_IDENTITY_MISMATCH")
    base = "/var/lib/ansible/site-data/" + site
    meta = yaml.safe_load(pct("cat", base + "/group_vars/all.yml"))["site"]
    data = yaml.safe_load(pct("cat", base + "/media_vm.yml"))["media_vm"]
    pve = yaml.safe_load(pct("cat", base + "/pve.yml"))["pve"]
    need(meta["id"] == site and meta["owner_slug"] + "-" + meta["type"] == site,
         "SITE_DATA_MISMATCH")
    need(pve["host"] == "192.168.11.30" and pve["node"] == "pve",
         "PVE_HOST_MISMATCH")
    return data, pve


def qm_version():
    return Path("/usr/sbin/qm") if Path("/usr/sbin/qm").is_file() else None


def check(site, data):
    vmid = int(data["vmid"])
    name = data["name"]
    need(2200 <= vmid <= 2254, "VMID_INVALID")
    need(name == "media-server-" + site, "VM_NAME_INVALID")
    need(data["os"]["distribution"] == "debian"
         and int(data["os"]["major_version"]) == 13, "OS_INVALID")
    need(data["network"]["bridge"] == "vmbr0", "BRIDGE_INVALID")
    ip = data["network"]["ipv4"]
    need(re.fullmatch(r"192\\.168\\.11\\.(2[0-4][0-9]|25[0-4])/24", ip) is not None,
         "STATIC_IP_INVALID")
    need(vmid == 2000 + int(ip.split(".")[-1].split("/")[0]), "VMID_IP_MISMATCH")
    for dev in ("gpu", "sata_controller"):
        need(BDF_PATTERN.fullmatch(data["pci"][dev]) is not None,
             "PCI_ADDRESS_INVALID")
        slot = data["pci"][dev][5:]
        need(Path("/sys/bus/pci/devices/" + data["pci"][dev]).exists(),
             "PCI_DEVICE_MISSING_" + dev)
    need(data["pci"]["gpu"] != data["pci"]["sata_controller"], "PCI_DUPLICATE")
    approval = data["approvals"]
    need(approval.get("provision") is True, "PROVISION_NOT_APPROVED")
    need(approval.get("gpu_cutover") is True
         and approval.get("sata_cutover") is True, "PCI_NOT_APPROVED")
    need(Path("/etc/pve/qemu-server/" + str(vmid) + ".conf").exists() is False
         or config(vmid).get("name") == name, "FOREIGN_VMID_OCCUPIED")
    for path in Path("/etc/pve/qemu-server").glob("*.conf"):
        if path.name == str(vmid) + ".conf":
            continue
        text = path.read_text()
        for dev in ("gpu", "sata_controller"):
            need(data["pci"][dev] not in text, "PCI_OWNED_BY_VM_" + path.stem)
    need("local-lvm" in run("/usr/sbin/pvesm", "status"), "STORAGE_MISSING")
    return vmid, name, ip


def download_verified():
    CACHE.mkdir(parents=True, exist_ok=True)
    manifest = urllib.request.urlopen(IMAGE_URL + "SHA512SUMS", timeout=30).read(40000).decode()
    candidates = [line.split() for line in manifest.splitlines()
                  if line.rstrip().endswith(" " + IMAGE_NAME)]
    need(len(candidates) == 1 and re.fullmatch(r"[0-9a-f]{128}", candidates[0][0]),
         "DEBIAN_CHECKSUM_NOT_FOUND")
    expected = candidates[0][0]
    target = CACHE / IMAGE_NAME

    def digest(path):
        h = hashlib.sha512()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                h.update(block)
        return h.hexdigest()

    if target.is_file() and digest(target) == expected:
        print("DEBIAN_IMAGE=VERIFIED_CACHED", flush=True)
        return target, expected
    fd, stage_name = tempfile.mkstemp(prefix="debian-", suffix=".partial", dir=CACHE)
    stage = Path(stage_name)
    try:
        checksum = hashlib.sha512()
        length = 0
        with os.fdopen(fd, "wb") as dest:
            with urllib.request.urlopen(IMAGE_URL + IMAGE_NAME, timeout=90) as src:
                while True:
                    block = src.read(1024 * 1024)
                    if not block:
                        break
                    dest.write(block)
                    checksum.update(block)
                    length += len(block)
                    need(length < 1_500_000_000, "IMAGE_SIZE_EXCEEDED")
            dest.flush()
            os.fsync(dest.fileno())
        need(length > 50_000_000, "IMAGE_TRUNCATED")
        need(checksum.hexdigest() == expected, "DEBIAN_IMAGE_SHA512_MISMATCH")
        os.replace(stage, target)
    finally:
        if stage.exists():
            stage.unlink()
    print("DEBIAN_IMAGE=DOWNLOADED_VERIFIED_SHA512", flush=True)
    return target, expected


def ensure_disk(vmid, image):
    state = config(vmid)
    if "scsi0" not in state:
        if "unused0" not in state:
            print("IMPORTING_DEBIAN_DISK=START", flush=True)
            qm("importdisk", vmid, str(image), "local-lvm", timeout=900)
            state = config(vmid)
        need("unused0" in state, "IMPORTED_DISK_MISSING")
        volume = state["unused0"].split(",", 1)[0]
        need(volume.startswith("local-lvm:vm-" + str(vmid) + "-disk-"),
             "IMPORTED_DISK_UNEXPECTED")
        qm("set", vmid, "--scsi0", volume + ",discard=on,iothread=1")
    state = config(vmid)
    need(state.get("scsi0", "").startswith("local-lvm:vm-" + str(vmid) + "-disk-"),
         "FOREIGN_BOOT_DISK")
    match = re.search(r"(?:^|,)size=(\\d+)([GMT])", state["scsi0"])
    if match:
        current = int(match[1]) * {"G": 1, "M": 1/1024, "T": 1024}[match[2]]
        if current < 64:
            qm("resize", vmid, "scsi0", "64G")
    if "ide2" not in config(vmid):
        qm("set", vmid, "--ide2", "local-lvm:cloudinit")


def ensure_config(vmid, site, data):
    state = config(vmid)
    need(state.get("name") == data["name"], "VM_NAME_DRIFT")
    need(state.get("lock") is None, "VM_LOCKED")
    if "net0" not in state:
        qm("set", vmid, "--net0", "virtio,bridge=vmbr0")
    if "serial0" not in config(vmid):
        qm("set", vmid, "--serial0", "socket")
    if "vga" not in config(vmid):
        qm("set", vmid, "--vga", "serial0")
    qm("set", vmid, "--ciuser", "debian")
    qm("set", vmid, "--ipconfig0",
       "ip=" + data["network"]["ipv4"] + ",gw=192.168.11.1")
    qm("set", vmid, "--nameserver", "192.168.11.1")
    pubkey = pct("ssh-keygen", "-y", "-f",
                 "/var/lib/ansible/.ssh/pve_host_write_ed25519")
    need(pubkey.startswith("ssh-ed25519 "), "SSH_PUBLIC_KEY_UNAVAILABLE")
    fd, name = tempfile.mkstemp(prefix="dh-media-key-", dir="/run")
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(pubkey + "\n")
        qm("set", vmid, "--sshkeys", name)
    finally:
        Path(name).unlink(missing_ok=True)
    for slot, dev in enumerate(("gpu", "sata_controller")):
        key = "hostpci" + str(slot)
        value = data["pci"][dev] + ",pcie=1"
        existing = config(vmid).get(key)
        need(existing is None or existing == value, "PCI_GUEST_DRIFT_" + key)
        if existing is None:
            qm("set", vmid, "--" + key, value)
    qm("set", vmid, "--boot", "order=scsi0")
    qm("set", vmid, "--agent", "enabled=1")
    qm("set", vmid, "--onboot", "0")
    if data.get("lifecycle", {}).get("protection", True):
        qm("set", vmid, "--protection", "1")
    qm("cloudinit", "update", vmid)


def apply(site):
    data, _ = site_data(site)
    vmid, name, ip = check(site, data)
    existing = Path("/etc/pve/qemu-server/" + str(vmid) + ".conf").exists()
    if existing:
        need(config(vmid).get("name") == name, "EXISTING_VM_MISMATCH")
        if qm("status", vmid) == "status: running":
            print("MEDIA_VM=ALREADY_RUNNING", flush=True)
            return
    print("TARGET_VMID=" + str(vmid), flush=True)
    print("GUEST_IP=" + ip, flush=True)
    image, checksum = download_verified() if not existing or "scsi0" not in config(vmid) else (None, None)
    if not existing:
        qm("create", vmid, "--name", name,
           "--ostype", "l26", "--machine", "q35", "--bios", "seabios",
           "--cores", "4", "--memory", "4096", "--scsihw", "virtio-scsi-single",
           "--net0", "virtio,bridge=vmbr0", "--serial0", "socket",
           "--vga", "serial0", "--onboot", "0")
        print("VM_CREATED=YES", flush=True)
    ensure_disk(vmid, image)
    ensure_config(vmid, site, data)
    qm("start", vmid, timeout=120)
    need(qm("status", vmid) == "status: running", "VM_START_FAILED")
    print("MEDIA_VM=RUNNING", flush=True)
    print("MEDIA_VM_PCI=ATTACHED", flush=True)
    print("LEGACY_VMS=UNTOUCHED", flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--site", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    data, _ = site_data(args.site)
    vmid, name, ip = check(args.site, data)
    if not args.apply:
        print("MEDIA_DEPLOY_PREFLIGHT=PASS VMID=" + str(vmid))
        return
    apply(args.site)


if __name__ == "__main__":
    try:
        main()
    except (Blocked, KeyError, TypeError, ValueError, subprocess.TimeoutExpired) as exc:
        print("MEDIA_DEPLOY=BLOCKED: " + (str(exc) if isinstance(exc, Blocked)
              else type(exc).__name__), file=sys.stderr)
        sys.exit(2)
