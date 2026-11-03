#!/usr/bin/env python3
"""Media ZFS disk lifecycle: one disk on the commissioned SATA controller.

The user chooses *action*, not drive identity. The controller's host-side
PCI vendor/device ID is measured by Task 80 on PVE and passed to this guest.
The guest verifies which disks have that SATA PCI parent in Linux sysfs.
No manufacturer, serial, WWN, old filesystem, partition GUID or ZFS pool
GUID is an erase prerequisite. This script never operates on the PVE host.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys


class Blocked(Exception):
    pass


def require(ok, reason):
    if not ok:
        raise Blocked(reason)


def command(*argv, allow_failure=False):
    result = subprocess.run(argv, text=True, capture_output=True,
                            timeout=120, check=False)
    if result.returncode:
        if allow_failure:
            return None
        raise Blocked("COMMAND_FAILED_" + Path(argv[0]).name
                      + "_" + str(result.returncode))
    return result.stdout.strip()


def get_pool(name):
    output = command("/usr/sbin/zpool", "list", "-H", "-o",
                     "name,guid", allow_failure=True) or ""
    for line in output.splitlines():
        tokens = line.split()
        if len(tokens) == 2 and tokens[0] == name:
            return tokens[1]
    return None


def sata_pci_parent(name):
    """Resolve the REAL ancestor AHCI PCI function, not an sdX/virtio guess."""
    dev = (Path("/sys/class/block") / name / "device")
    require(dev.is_symlink(), "SATA_DISK_SYSFS_NOT_AVAILABLE")
    node = dev.resolve(strict=True)
    for ancestor in (node, *node.parents):
        class_file = ancestor / "class"
        if class_file.is_file():
            cls = class_file.read_text().strip().lower()
            if cls.startswith("0x0106"):
                return (ancestor / "vendor").read_text().strip().lower(), (
                    ancestor / "device").read_text().strip().lower()
    return None


def find_disk(data):
    require(Path("/etc/pve/user.cfg").exists() is False,
            "PVE_HOST_MUST_NOT_RUN_MEDIA_STORAGE")
    ids = data.get("sata_controller")
    require(isinstance(ids, dict)
            and re.fullmatch(r"0x[0-9a-f]{4}", ids.get("vendor", "")) is not None
            and re.fullmatch(r"0x[0-9a-f]{4}", ids.get("device", "")) is not None,
            "SATA_CONTROLLER_PCI_ID_NOT_COMMISSIONED")
    block = json.loads(command("/usr/bin/lsblk", "-J", "-b", "-d",
                               "-o", "NAME,PATH,TYPE,TRAN"))
    require(isinstance(block.get("blockdevices"), list),
            "SATA_BLOCK_DEVICE_LIST_INVALID")
    matches = []
    for dev in block["blockdevices"]:
        if dev.get("type") != "disk":
            continue
        name = dev.get("name", "")
        path = dev.get("path", "")
        if not re.fullmatch(r"sd[a-z]+", name):
            continue
        if path != "/dev/" + name or not Path(path).is_block_device():
            continue
        # The controller-class ancestor is authoritative; Linux transport
        # alone could also be reported for a disk on a DIFFERENT controller.
        if sata_pci_parent(name) == (ids["vendor"], ids["device"]):
            matches.append(path)
    print("MEDIA_SATA_DISKS_FOUND=" + str(len(matches)), flush=True)
    require(matches, "SATA_NO_DISKS")
    require(len(matches) == 1, "SATA_MULTIPLE_DISKS")
    disk = matches[0]
    root = command("/usr/bin/findmnt", "-n", "-o", "SOURCE", "/")
    ancestors = command("/usr/bin/lsblk", "-s", "-n", "-o", "KNAME",
                        root).split()
    require(Path(disk).name not in ancestors, "SATA_DISK_IS_SYSTEM_DISK")
    return disk, disk


def leaf_disk(path):
    """Resolve a ZFS leaf /dev/sdX1 to its physical parent /dev/sdX."""
    resolved = str(Path(path).resolve())
    nodes = command("/usr/bin/lsblk", "-s", "-n", "-o",
                    "NAME,TYPE", resolved).splitlines()
    disks = [row.split()[0] for row in nodes
             if len(row.split()) == 2 and row.split()[1] == "disk"]
    require(len(disks) == 1, "ZFS_VDEV_PARENT_AMBIGUOUS")
    return "/dev/" + disks[0]


def pool_leaves(name):
    report = command("/usr/sbin/zpool", "status", "-LP", name)
    return [leaf_disk(m.group(1)) for m in re.finditer(
        r"(?m)^\s+(/dev/\S+)\s+(?:ONLINE|DEGRADED|OFFLINE|FAULTED|UNAVAIL|REMOVED)\b",
        report)]


def verify_pool_disk(name, physical):
    leaves = pool_leaves(name)
    require(len(leaves) == 1 and leaves[0] == physical,
            "ZFS_VDEV_NOT_THE_SOLE_SATA_DISK")


def scan_devices(physical):
    """Only the selected SATA disk and its direct partitions, never /dev/*."""
    tree = json.loads(command("/usr/bin/lsblk", "-J", "-T",
                              "-o", "PATH,TYPE", physical))
    roots = tree.get("blockdevices", [])
    require(len(roots) == 1 and roots[0].get("path") == physical
            and roots[0].get("type") == "disk",
            "SATA_DISK_TOPOLOGY_INVALID")
    children = roots[0].get("children", [])
    require(isinstance(children, list), "SATA_DISK_TOPOLOGY_INVALID")
    result = [physical]
    for part in children:
        require(part.get("type") == "part"
                and part.get("path", "").startswith(physical)
                and not part.get("children"),
                "SATA_DISK_PARTITION_TOPOLOGY_INVALID")
        result.append(part["path"])
    return result


def importable_report(physical):
    argv = ["/usr/sbin/zpool", "import"]
    for dev in scan_devices(physical):
        argv += ["-d", dev]
    return command(*argv, allow_failure=True) or ""


def ensure_import(pool, disk, physical, access, expected_guid=None):
    """Only import the selected SATA disk; never wipe/destroy on this path."""
    guid = get_pool(pool)
    if guid:
        verify_pool_disk(pool, physical)
        return guid, False
    report = importable_report(physical)
    sections = re.split(r"(?=^\s*pool:\s*)", report, flags=re.MULTILINE)
    matches = [chunk for chunk in sections if re.search(
        r"(?m)^\s*pool:\s*" + re.escape(pool) + r"\s*$", chunk)]
    require(len(matches) == 1, "REQUESTED_POOL_NOT_FOUND_OR_AMBIGUOUS")
    section = matches[0]
    require(re.search(r"(?m)^\s*state:\s*ONLINE\s*$", section),
            "REQUESTED_POOL_NOT_ONLINE")
    argv = ["/usr/sbin/zpool", "import", "-N"]
    for dev in scan_devices(physical):
        argv += ["-d", dev]
    if access == "readonly":
        argv += ["-o", "readonly=on"]
    if "last accessed by another system" in section:
        # PCI passthrough grants the VM exclusive controller ownership.
        # Never force a non-ONLINE or ambiguous pool.
        argv.append("-f")
    argv.append(pool)
    command(*argv)
    guid = get_pool(pool)
    require(guid is not None, "POOL_IMPORT_UNCONFIRMED")
    verify_pool_disk(pool, physical)
    print("POOL=IMPORTED", flush=True)
    return guid, True


def active_pools():
    text = command("/usr/sbin/zpool", "list", "-H", "-o",
                   "name", allow_failure=True) or ""
    return [name for name in text.splitlines() if name]


def erase_disk(data, physical):
    """Explicit erase: remove any old FS/ZFS on exactly this one SATA disk."""
    require(data.get("destructive_without_backup") is True,
            "DISK_ERASE_NOT_APPROVED")
    name = data["pool_name"]
    for pool in active_pools():
        leaves = pool_leaves(pool)
        if physical in leaves:
            require(len(leaves) == 1 and leaves[0] == physical,
                    "SATA_DISK_PART_OF_MULTI_VDEV_POOL")
            command("/usr/sbin/zpool", "destroy", "-f", pool)
            print("SATA_POOL=DESTROYED_" + pool, flush=True)
        elif pool == name:
            raise Blocked("TARGET_POOL_NAME_ACTIVE_ON_OTHER_DISK")
    for dev in reversed(scan_devices(physical)):
        mounted = command("/usr/bin/findmnt", "-rn", "--source", dev,
                          allow_failure=True)
        require(not mounted, "SATA_DISK_BUSY_MOUNTED")
        command("/usr/sbin/wipefs", "--all", "--force", dev)
    command("/usr/sbin/sgdisk", "--zap-all", physical)
    command("/usr/bin/udevadm", "settle")
    command("/usr/sbin/zpool", "create", "-f", "-o", "ashift=12",
            "-O", "compression=zstd", "-O", "atime=off",
            "-O", "xattr=sa", "-O", "acltype=posixacl",
            name, physical)
    guid = get_pool(name)
    require(guid is not None, "POOL_CREATE_UNCONFIRMED")
    verify_pool_disk(name, physical)
    print("POOL=CREATED", flush=True)
    return guid, True


def validate_layout(data):
    """Validate *before* any pool destruction or partition writes."""
    require(data["mode"] in ("readonly", "readwrite"), "INVALID_ACCESS_MODE")
    layout = data["layout"]
    datasets = layout["datasets"]
    directories = layout["directories"]
    require(isinstance(datasets, list) and datasets and "media" in datasets
            and len(set(datasets)) == len(datasets), "INVALID_DATASET_LIST")
    for name in datasets:
        require(isinstance(name, str)
                and re.fullmatch(r"[a-z][a-z0-9_-]{1,35}", name),
                "INVALID_DATASET_NAME")
    require(isinstance(directories, list)
            and len(set(directories)) == len(directories), "INVALID_DIRECTORY_LIST")
    for directory in directories:
        require(isinstance(directory, str)
                and re.fullmatch(r"[a-z][a-z0-9_-]{1,35}(?:/[a-z][a-z0-9_-]{1,35}){1,5}",
                                 directory), "INVALID_DIRECTORY_PATH")
        require(directory.split("/")[0] in datasets,
                "UNDECLARED_DATASET_DIRECTORY")


def reconcile_layout(data):
    pool = data["pool_name"]
    layout = data["layout"]
    datasets = layout["datasets"]
    directories = layout["directories"]
    require(isinstance(datasets, list) and datasets
            and len(set(datasets)) == len(datasets)
            and "media" in datasets, "INVALID_DATASET_LIST")
    for name in datasets:
        require(isinstance(name, str)
                and re.fullmatch("[a-z][a-z0-9_-]{1,35}", name),
                "INVALID_DATASET_NAME")
    for directory in directories:
        require(isinstance(directory, str)
                and re.fullmatch("[a-z][a-z0-9_-]{1,35}(?:/[a-z][a-z0-9_-]{1,35}){1,5}",
                                 directory),
                "INVALID_DIRECTORY_PATH")
        require(directory.split("/")[0] in datasets, "UNDECLARED_DATASET_DIRECTORY")
    modified = False
    readonly = data["mode"] == "readonly"
    for name in datasets:
        dataset = pool + "/" + name
        if command("/usr/sbin/zfs", "list", "-H", "-o", "name", dataset,
                   allow_failure=True) is None:
            require(not readonly, "READONLY_IMPORT_DATASET_MISSING_" + name)
            command("/usr/sbin/zfs", "create", dataset)
            modified = True
        if command("/usr/sbin/zfs", "get", "-H", "-o", "value",
                   "mounted", dataset) != "yes":
            command("/usr/sbin/zfs", "mount", dataset)
    for path in directories:
        dest = Path("/") / pool / path
        require(not dest.is_symlink(), "DIRECTORY_SYMLINK_BLOCKED")
        if not dest.exists():
            require(not readonly, "READONLY_IMPORT_DIRECTORY_MISSING")
            dest.mkdir(parents=True, exist_ok=True)
            modified = True
    print("LAYOUT=" + ("CREATED" if modified else "UNCHANGED"), flush=True)
    return modified


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--action", choices=("import", "erase"))
    parser.add_argument("--owner")
    parser.add_argument("--discover-only", action="store_true")
    args = parser.parse_args()
    require(os.geteuid() == 0, "ROOT_REQUIRED")
    require(args.config.is_file() and not args.config.is_symlink(),
            "CONFIG_MISSING")
    data = json.loads(args.config.read_text())
    require(re.fullmatch(r"[a-z][a-z0-9_-]{1,62}",
                         data["pool_name"]) is not None, "POOL_NAMING_INVALID")
    disk, physical = find_disk(data)
    if args.discover_only:
        print("MEDIA_SATA_DEVICE=" + physical, flush=True)
        return
    require(args.action in ("import", "erase"), "DISK_ACTION_REQUIRED")
    require(isinstance(args.owner, str)
            and re.fullmatch(r"[a-z][a-z0-9-]{1,30}", args.owner),
            "OWNER_SLUG_INVALID")
    if args.action == "erase":
        require(data["pool_name"] == args.owner + "-pool",
                "NEW_POOL_OWNER_NAMING_REQUIRED")
    require(data["media_dataset"] == data["pool_name"] + "/media",
            "CANONICAL_MEDIA_DATASET_MISMATCH")
    validate_layout(data)
    require(args.action != "erase" or data["mode"] == "readwrite",
            "ERASE_REQUIRES_READWRITE")
    command("/usr/sbin/modprobe", "zfs")
    if args.action == "erase":
        print("MEDIA_DISK_ACTION=ERASE", flush=True)
        guid, changed = erase_disk(data, physical)
    else:
        print("MEDIA_DISK_ACTION=IMPORT", flush=True)
        guid, changed = ensure_import(data["pool_name"], disk, physical,
                                      data["mode"])
    expected_access = "on" if data["mode"] == "readonly" else "off"
    require(command("/usr/sbin/zpool", "get", "-H", "-o", "value",
                    "readonly", data["pool_name"]) == expected_access,
            "POOL_ACCESS_MODE_MISMATCH")
    reconcile_layout(data)
    print("POOL_GUID=" + guid, flush=True)
    print("MEDIA_STORAGE_RECONCILED=PASS", flush=True)


if __name__ == "__main__":
    try:
        main()
    except (Blocked, OSError, KeyError, TypeError, ValueError,
            json.JSONDecodeError, subprocess.TimeoutExpired) as exc:
        cause = str(exc) if isinstance(exc, Blocked) else type(exc).__name__
        print("MEDIA_STORAGE_RECONCILED=BLOCKED_" + cause, file=sys.stderr)
        sys.exit(2)
