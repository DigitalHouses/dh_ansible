#!/usr/bin/env python3
"""PVE fixed-purpose Debian 13 media VM provisioning (one canonical site VM).

Called from the fixed-purpose PVE root worker. VM create/recreate/keep is
independent of raw-disk import/erase. Physical-disk modifications happen
only inside the separately verified guest ZFS reconciler.
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
MANAGED_MEDIA_DESCRIPTION = "DigitalHouses managed media guest v2"
SITE_PATTERN = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)+\Z")
BDF_PATTERN = re.compile(r"[0-9a-f]{4}:[0-9a-f]{2}:[0-9a-f]{2}\.[0-7]\Z")


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
    need(pve["node"] == run("/usr/bin/hostname", "-s"), "PVE_NODE_MISMATCH")
    need(pve["host"] in run("/usr/sbin/ip", "-4", "-o", "addr", "show"),
         "PVE_HOST_MISMATCH")
    return data, pve


def qm_version():
    return Path("/usr/sbin/qm") if Path("/usr/sbin/qm").is_file() else None


def check(site, data, pve):
    vmid = int(data["vmid"])
    name = data["name"]
    need(2200 <= vmid <= 2254, "VMID_INVALID")
    need(name == "mediaserver-" + site.rsplit("-", 1)[0], "VM_NAME_INVALID")
    need(data["os"]["distribution"] == "debian"
         and int(data["os"]["major_version"]) == 13, "OS_INVALID")
    need(data["network"]["bridge"] == "vmbr0", "BRIDGE_INVALID")
    ip = data["network"]["ipv4"]
    need(re.fullmatch(r"[0-9]{1,3}(\.[0-9]{1,3}){3}/24", ip) is not None,
         "STATIC_IP_INVALID")
    need(ip.split("/")[0].split(".")[:3] == pve["host"].split(".")[:3],
         "STATIC_IP_SUBNET_MISMATCH")
    need(vmid == 2000 + int(ip.split(".")[-1].split("/")[0]), "VMID_IP_MISMATCH")
    res = data.get("resources", {})
    need(1 <= int(res.get("cores", 4)) <= 8 and 2048 <= int(res.get("memory_mb", 4096)) <= 8192
         and 32 <= int(res.get("disk_gb", 64)) <= 256, "VM_RESOURCES_INVALID")
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
    existing_config = Path("/etc/pve/qemu-server/" + str(vmid) + ".conf")
    if existing_config.exists():
        current = config(vmid)
        legacy_name = "media-server-" + site
        need(current.get("name") == name or
             (current.get("name") == legacy_name and
              current.get("description") == MANAGED_MEDIA_DESCRIPTION and
              current.get("hostpci0") == data["pci"]["gpu"] + ",pcie=1" and
              current.get("hostpci1") == data["pci"]["sata_controller"] + ",pcie=1"),
             "FOREIGN_VMID_OCCUPIED")
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


def ensure_disk(vmid, image, disk_gb):
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
    match = re.search(r"(?:^|,)size=(\d+)([GMT])", state["scsi0"])
    if match:
        current = int(match[1]) * {"G": 1, "M": 1/1024, "T": 1024}[match[2]]
        if current < disk_gb:
            qm("resize", vmid, "scsi0", str(disk_gb) + "G")
    if "ide2" not in config(vmid):
        qm("set", vmid, "--ide2", "local-lvm:cloudinit")


def media_display(data):
    """Prefer reliable PVE xterm.js when a GPU is passed into the media guest.

    A PCI GPU console cannot be mirrored in noVNC; using VGA std with
    passthrough freezes the PVE framebuffer during i915 init.
    Existing site data may still request std, but the effective safety
    policy for this managed GPU VM is serial0.
    """
    requested = data.get("lifecycle", {}).get("display", "serial0")
    need(requested in ("std", "serial0"), "DISPLAY_INVALID")
    return "serial0" if data.get("pci", {}).get("gpu") else requested


def ensure_lifecycle(vmid, data):
    """Reconcile requested autostart and console even for a running VM."""
    lifecycle = data.get("lifecycle", {})
    display = media_display(data)
    wanted_boot = "1" if lifecycle.get("onboot", False) else "0"
    current = config(vmid)
    if current.get("onboot", "0") != wanted_boot:
        qm("set", vmid, "--onboot", wanted_boot)
    if current.get("vga", "serial0") != display:
        qm("set", vmid, "--vga", display)


def ensure_config(vmid, site, data):
    state = config(vmid)
    need(state.get("name") == data["name"], "VM_NAME_DRIFT")
    need(state.get("lock") is None, "VM_LOCKED")
    if "net0" not in state:
        qm("set", vmid, "--net0", "virtio,bridge=vmbr0")
    if "serial0" not in config(vmid):
        qm("set", vmid, "--serial0", "socket")
    qm("set", vmid, "--ciuser", "debian")
    gateway = data["network"].get("gateway")
    need(gateway and re.fullmatch(r"[0-9.]+", gateway), "GATEWAY_NOT_DECLARED")
    qm("set", vmid, "--ipconfig0",
       "ip=" + data["network"]["ipv4"] + ",gw=" + gateway)
    qm("set", vmid, "--nameserver", gateway)
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
    ensure_lifecycle(vmid, data)
    if data.get("lifecycle", {}).get("protection", True):
        qm("set", vmid, "--protection", "1")
    qm("cloudinit", "update", vmid)


def guest_exec(vmid, *args):
    """Verified QEMU agent command, without host shell or SSH credentials."""
    result = json.loads(qm("guest", "exec", vmid, "--", *args, timeout=45))
    need(result.get("exited") == 1 and result.get("exitcode") == 0,
         "MEDIA_GUEST_AGENT_PREFLIGHT_FAILED")
    return result.get("out-data", "").strip()


def old_guest_matches(vmid, site, data, *, managed=False):
    """Read-only, exact disk and pool identity check before VM deletion."""
    storage = data["storage"]
    state = config(vmid)
    need(state.get("name") in ("media-server-" + site, data["name"]),
         "FOREIGN_VM_BLOCKED")
    need(state.get("scsi0", "").startswith(
        "local-lvm:vm-" + str(vmid) + "-disk-"), "FOREIGN_BOOT_VOLUME")
    need(state.get("hostpci0") == data["pci"]["gpu"] + ",pcie=1",
         "GPU_CONFIG_DRIFT")
    need(state.get("hostpci1") == data["pci"]["sata_controller"] + ",pcie=1",
         "SATA_CONFIG_DRIFT")
    need(state.get("lock") is None, "VM_LOCKED")
    for slot in ("scsi1", "scsi2", "sata0", "sata1", "virtio0",
                 "virtio1", "ide0", "ide1", "unused0", "unused1"):
        need(slot not in state, "UNEXPECTED_VM_DISK_" + slot)
    devices = json.loads(guest_exec(vmid, "/usr/bin/lsblk",
                                    "-J", "-b", "-d", "-o",
                                    "NAME,PATH,TYPE,MODEL,SERIAL,WWN,SIZE"))
    disks = devices.get("blockdevices", [])
    matches = [d for d in disks if
               d.get("type") == "disk"
               and str(d.get("serial", "")).strip() == storage["disk"]["serial"]
               and str(d.get("wwn", "")).lower() == storage["disk"]["wwn"].lower()
               and int(d.get("size") or 0) == int(storage["disk"]["size_bytes"])]
    need(len(matches) == 1 and matches[0].get("name") != "sda",
         "LEGACY_PHYSICAL_DISK_IDENTITY_MISMATCH")
    if managed:
        need(state.get("description") == MANAGED_MEDIA_DESCRIPTION,
             "MANAGED_VM_MARKER_MISSING")
        # A recreated managed guest may still hold the previous pool name.
        # The new pool name is used only *after* erase. Never infer or
        # silently accept a foreign pool: the source must be Fleet-pinned
        # by exact name and GUID, and the vdev must match the approved SSD.
        available = {}
        for line in guest_exec(vmid, "/usr/sbin/zpool", "list",
                               "-H", "-o", "name,guid").splitlines():
            parts = line.split()
            need(len(parts) == 2 and parts[1].isdecimal(),
                 "MANAGED_ZFS_INVENTORY_INVALID")
            available[parts[0]] = parts[1]
        desired = storage["pool_name"]
        previous = storage.get("recreate_source_pool")
        if desired in available:
            pool, expected_guid = desired, None
        else:
            need(isinstance(previous, dict)
                 and set(previous) == {"name", "guid"}
                 and isinstance(previous["name"], str)
                 and re.fullmatch(r"[a-z][a-z0-9_-]{1,62}",
                                  previous["name"]) is not None
                 and isinstance(previous["guid"], str)
                 and previous["guid"].isdecimal()
                 and previous["name"] != desired
                 and available.get(previous["name"]) == previous["guid"],
                 "MANAGED_ZFS_SOURCE_NOT_APPROVED")
            pool, expected_guid = previous["name"], previous["guid"]
    else:
        old = storage["legacy_pool"]
        pool = old["name"]
        expected_guid = str(old["guid"])
    observed = guest_exec(vmid, "/usr/sbin/zpool", "get",
                          "-H", "-o", "value", "guid", pool)
    need(observed.isdecimal() and (expected_guid is None or observed == expected_guid),
         "LEGACY_ZFS_GUID_MISMATCH")
    # Match legacy ZFS's sole vdev to the approved SSD, not merely a pool
    # with the correct GUID on another disk of the passed SATA controller.
    status = guest_exec(vmid, "/usr/sbin/zpool", "status", "-LP", pool)
    vdevs = []
    for line in status.splitlines():
        column = line.strip().split()
        if column and column[0].startswith("/dev/"):
            vdevs.append(column[0])
    need(len(vdevs) == 1, "LEGACY_ZFS_MULTIPLE_OR_MISSING_VDEVS")
    vdev = vdevs[0]
    parent = guest_exec(vmid, "/usr/bin/lsblk", "-n", "-o", "PKNAME", vdev)
    disk_path = matches[0].get("path")
    need(disk_path == "/dev/" + parent or
         (not parent and disk_path == vdev), "LEGACY_ZFS_ON_FOREIGN_DISK")
    print("DESTRUCTIVE_PREFLIGHT=PASS", flush=True)


def reconcile_media_vm_lifecycle(vmid, site, data, vm_action, disk_action):
    """Exactly one VM lifecycle intent. Never implicitly recreate a VM."""
    need(vm_action in ("create", "recreate", "keep"), "VM_ACTION_INVALID")
    need(disk_action in ("import", "erase"), "RAW_DISK_ACTION_INVALID")
    exists = Path("/etc/pve/qemu-server/" + str(vmid) + ".conf").exists()
    if vm_action == "create":
        need(not exists, "VM_CREATE_REQUIRES_ABSENT_VM")
        print("MEDIA_VM=ABSENT_CREATE_FRESH", flush=True)
        return
    need(exists, "VM_" + vm_action.upper() + "_REQUIRES_EXISTING_VM")
    state = config(vmid)
    legacy_name = "media-server-" + site
    need(state.get("name") in (legacy_name, data["name"]), "FOREIGN_VM_BLOCKED")
    if vm_action == "keep":
        if state.get("name") == legacy_name:
            need(state.get("description") == MANAGED_MEDIA_DESCRIPTION,
                 "LEGACY_MEDIA_VM_UNMANAGED")
            old_guest_matches(vmid, site, data, managed=True)
            qm("set", vmid, "--name", data["name"])
            need(config(vmid).get("name") == data["name"],
                 "MEDIA_VM_RENAME_UNCONFIRMED")
            print("MEDIA_VM=RENAMED_VERIFIED_LEGACY", flush=True)
        print("MEDIA_VM=EXISTS_PRESERVED", flush=True)
        return
    need(qm("status", vmid) == "status: running",
         "VM_RECREATE_REQUIRES_RUNNING_VERIFIABLE_GUEST")
    managed = state.get("description") == MANAGED_MEDIA_DESCRIPTION
    # A managed marker is insufficient: use QGA to verify both the approved
    # SSD and sole ZFS vdev before touching a VM's local-lvm system disk.
    old_guest_matches(vmid, site, data, managed=managed)
    if not managed:
        need(disk_action == "erase", "LEGACY_POOL_REQUIRES_EXPLICIT_ERASE")
    if disk_action == "erase":
        need(data["storage"].get("destructive_without_backup") is True,
             "DISK_ERASE_NOT_APPROVED_IN_FLEET")
    # Only the exact VM's PVE disks are deleted. Raw PCI disk is untouched
    # here regardless of the independently chosen raw-disk action.
    try:
        qm("set", vmid, "--protection", "0")
        qm("shutdown", vmid, "--timeout", "120", timeout=135)
        need(qm("status", vmid) == "status: stopped",
             "GUEST_SHUTDOWN_UNCONFIRMED")
        qm("destroy", vmid, "--purge", "1", "--destroy-unreferenced-disks", "1",
           timeout=150)
        need(not Path("/etc/pve/qemu-server/" + str(vmid) + ".conf").exists(),
             "GUEST_DELETION_UNCONFIRMED")
    finally:
        if Path("/etc/pve/qemu-server/" + str(vmid) + ".conf").exists():
            qm("set", vmid, "--protection", "1")
    print("VM_RECREATE=REMOVED_VERIFIED_" + ("MANAGED" if managed else "LEGACY"),
          flush=True)


def apply(site, vm_action, disk_action):
    data, pve = site_data(site)
    vmid, name, ip = check(site, data, pve)
    reconcile_media_vm_lifecycle(vmid, site, data, vm_action, disk_action)
    existing = Path("/etc/pve/qemu-server/" + str(vmid) + ".conf").exists()
    if existing:
        need(config(vmid).get("name") == name, "EXISTING_VM_MISMATCH")
        if qm("status", vmid) == "status: running":
            ensure_lifecycle(vmid, data)
            print("MEDIA_VM=ALREADY_RUNNING", flush=True)
            return
    print("TARGET_VMID=" + str(vmid), flush=True)
    print("GUEST_IP=" + ip, flush=True)
    image, _ = download_verified() if not existing or "scsi0" not in config(vmid) else (None, None)
    if not existing:
        qm("create", vmid, "--name", name,
           "--ostype", "l26", "--machine", "q35", "--bios", "seabios",
           "--cores", str(data.get("resources", {}).get("cores", 4)),
           "--memory", str(data.get("resources", {}).get("memory_mb", 4096)),
           "--scsihw", "virtio-scsi-single",
           "--net0", "virtio,bridge=vmbr0", "--serial0", "socket",
           "--vga", media_display(data),
           "--onboot", "1" if data.get("lifecycle", {}).get("onboot") else "0",
           "--description", MANAGED_MEDIA_DESCRIPTION)
        print("VM_CREATED=YES", flush=True)
    ensure_disk(vmid, image, int(data.get("resources", {}).get("disk_gb", 64)))
    ensure_config(vmid, site, data)
    qm("start", vmid, timeout=120)
    need(qm("status", vmid) == "status: running", "VM_START_FAILED")
    print("MEDIA_VM=RUNNING", flush=True)
    print("MEDIA_VM_PCI=ATTACHED", flush=True)
    print("LEGACY_VMS=UNTOUCHED", flush=True)



def ensure_backup_storage(site, data, pve):
    """Map only a site-verified media guest NFS export as PVE backup storage."""
    vmid = int(data["vmid"])
    backup = data.get("backup_export", {})
    storage_id = backup.get("storage_id", "")
    nfs_server = data["network"]["ipv4"].split("/")[0]
    nfs_export = "/" + data["storage"]["pool_name"] + "/backups/pve"
    need(storage_id == "dh_media_backups" and
         backup.get("path") == nfs_export and
         backup.get("pve_host") == pve["host"],
         "MEDIA_BACKUP_STORAGE_FLEET_MISMATCH")
    need(qm("status", vmid) == "status: running",
         "MEDIA_NFS_BACKUP_SERVER_VM_NOT_RUNNING")
    need(config(vmid).get("name") == data["name"],
         "MEDIA_BACKUP_SERVER_VM_IDENTITY_MISMATCH")
    storage_cfg = Path("/etc/pve/storage.cfg")
    need(storage_cfg.is_file(), "PVE_STORAGE_CONFIG_ABSENT")
    contents = storage_cfg.read_text()
    headers = re.findall(r"(?m)^[a-z][a-z0-9]*:\s+([^\s]+)", contents)
    if storage_id in headers:
        block = re.search(r"(?m)^nfs:\s+" + re.escape(storage_id) +
                          r"\s*\n((?:[ \t]+[^\n]*\n)*)", contents)
        need(block is not None, "BACKUP_STORAGE_ID_COLLISION")
        fields = dict(line.strip().split(None, 1)
                      for line in block.group(1).splitlines() if line.strip())
        need(fields.get("server") == nfs_server and
             fields.get("export") == nfs_export and
             fields.get("content") == "backup",
             "BACKUP_STORAGE_CONFIG_DRIFT")
        print("MEDIA_PVE_NFS_BACKUP=UNCHANGED", flush=True)
        return
    scan = run("/usr/sbin/pvesm", "scan", "nfs", nfs_server, timeout=50)
    need(any(line.split() and line.split()[0] == nfs_export
             for line in scan.splitlines()),
         "MEDIA_NFS_BACKUP_EXPORT_NOT_VISIBLE")
    run("/usr/sbin/pvesm", "add", "nfs", storage_id,
        "--server", nfs_server, "--export", nfs_export,
        "--content", "backup", "--options", "vers=4.1",
        timeout=90)
    print("MEDIA_PVE_NFS_BACKUP=CREATED", flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--site", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--vm-action", choices=("create", "recreate", "keep"), required=True)
    parser.add_argument("--raw-disk-action", choices=("import", "erase"), required=True)
    parser.add_argument("--map-backups", action="store_true",
                        help="Register only this approved NFS backup storage after guest setup")
    parser.add_argument("--verify-recreate", action="store_true",
                        help="Read-only PVE/QGA/ZFS identity gate; never changes VM or disks")
    args = parser.parse_args()
    data, pve = site_data(args.site)
    vmid, name, ip = check(args.site, data, pve)
    if args.map_backups:
        need(args.apply and not args.verify_recreate,
             "BACKUP_STORAGE_APPLY_REQUIRED")
        ensure_backup_storage(args.site, data, pve)
        return
    exists = Path("/etc/pve/qemu-server/" + str(vmid) + ".conf").exists()
    if args.vm_action == "create":
        need(not exists, "VM_CREATE_REQUIRES_ABSENT_VM")
    else:
        need(exists, "VM_" + args.vm_action.upper() + "_REQUIRES_EXISTING_VM")
    if args.verify_recreate:
        need(not args.apply and args.vm_action == "recreate",
             "READ_ONLY_RECREATE_PREFLIGHT_ONLY")
        old_guest_matches(vmid, args.site, data,
                          managed=config(vmid).get("description") ==
                                  MANAGED_MEDIA_DESCRIPTION)
        print("MEDIA_VM_RECREATE_READ_ONLY_PREFLIGHT=PASS", flush=True)
        return
    if not args.apply:
        print("MEDIA_DEPLOY_PREFLIGHT=PASS VMID=" + str(vmid))
        return
    apply(args.site, args.vm_action, args.raw_disk_action)


if __name__ == "__main__":
    try:
        main()
    except (Blocked, KeyError, TypeError, ValueError, json.JSONDecodeError, subprocess.TimeoutExpired) as exc:
        print("MEDIA_DEPLOY=BLOCKED: " + (str(exc) if isinstance(exc, Blocked)
              else type(exc).__name__), file=sys.stderr)
        sys.exit(2)
