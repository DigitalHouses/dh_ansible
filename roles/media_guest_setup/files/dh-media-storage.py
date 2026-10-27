#!/usr/bin/env python3
"""Idempotent DigitalHouses media storage resource reconciler.

The program never selects a disk by /dev/sdX name. It receives declarative
disk and pool identities and a one-run create/import choice from Semaphore.
All irreversible writes require exactly identified legacy ZFS or an empty disk.
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


def require(condition, reason):
    if not condition:
        raise Blocked(reason)


def command(*argv, allow_failure=False):
    p = subprocess.run(argv, text=True, capture_output=True, timeout=120,
                       check=False)
    if p.returncode and not allow_failure:
        raise Blocked("COMMAND_FAILED_" + Path(argv[0]).name + "_" + str(p.returncode))
    return p.stdout.strip() if p.returncode == 0 else None


def get_pool(name):
    output = command("/usr/sbin/zpool", "list", "-H", "-o", "name,guid",
                     allow_failure=True)
    pools = {}
    for line in (output or "").splitlines():
        fields = line.split()
        if len(fields) == 2:
            pools[fields[0]] = fields[1]
    return pools.get(name)


def find_disk(spec):
    devices = json.loads(command("/usr/bin/lsblk", "-J", "-b", "-d", "-o",
                                 "NAME,PATH,TYPE,MODEL,SERIAL,WWN,SIZE"))
    matches = [d for d in devices["blockdevices"] if
               d.get("type") == "disk"
               and str(d.get("serial") or "").strip() == spec["serial"]
               and str(d.get("wwn") or "").lower() == spec["wwn"].lower()
               and int(d.get("size") or 0) == int(spec["size_bytes"])]
    require(len(matches) == 1, "DATA_DISK_IDENTITY_MISMATCH")
    disk = matches[0]["path"]
    link = Path("/dev/disk/by-id/wwn-" + spec["wwn"].lower())
    require(link.is_symlink() and link.resolve() == Path(disk).resolve(),
            "DATA_DISK_WWN_SYMLINK_MISMATCH")
    root_source = command("/usr/bin/findmnt", "-no", "SOURCE", "/")
    root_parent = command("/usr/bin/lsblk", "-no", "PKNAME", root_source) or ""
    require(root_parent and disk != "/dev/" + root_parent,
            "DATA_DISK_IS_SYSTEM_DISK")
    require(Path("/etc/pve/user.cfg").exists() is False,
            "PVE_HOST_MUST_NOT_RUN_MEDIA_STORAGE")
    return str(link), str(Path(disk).resolve())


def leaf_disk(path):
    resolved = str(Path(path).resolve())
    if resolved.startswith("/dev/"):
        parent = command("/usr/bin/lsblk", "-n", "-o", "PKNAME", resolved,
                         allow_failure=True)
        return "/dev/" + parent if parent else resolved
    return ""


def verify_pool_disk(name, expected_path):
    status = command("/usr/sbin/zpool", "status", "-LP", name)
    leaves = []
    for line in status.splitlines():
        part = line.strip().split()
        if part and part[0].startswith("/dev/"):
            leaves.append(leaf_disk(part[0]))
    require(len(leaves) == 1 and leaves[0] == expected_path,
            "ZFS_VDEV_NOT_THE_DECLARED_SINGLE_DATA_DISK")


def imported_guid_visible(name):
    report = command("/usr/sbin/zpool", "import", "-d", "/dev/disk/by-id",
                     allow_failure=True) or ""
    sections = re.split(r"(?=^\s*pool:\s*)", report, flags=re.MULTILINE)
    matches = []
    for section in sections:
        pool_match = re.search(r"^\s*pool:\s*(\S+)", section, re.MULTILINE)
        guid_match = re.search(r"^\s*id:\s*(\d+)", section, re.MULTILINE)
        if pool_match and pool_match.group(1) == name and guid_match:
            matches.append(guid_match.group(1))
    require(len(matches) <= 1, "DUPLICATE_IMPORTABLE_POOL")
    return matches[0] if matches else None


def allow_force_import_of_sole_verified_vdev(pool, disk):
    """Permit -f only for an exported prior-VM pool on the exact approved WWN.

    Recreating a VM changes the hostid recorded by ZFS. An ordinary import
    may then fail even though PCI ownership has moved to the new sole guest.
    Reject unknown topologies and all other import failures rather than
    blindly forcing an import from a potentially active foreign system.
    """
    report = command("/usr/sbin/zpool", "import", "-d", "/dev/disk/by-id",
                     allow_failure=True) or ""
    sections = re.split(r"(?=^\s*pool:\s*)", report, flags=re.MULTILINE)
    matches = [section for section in sections
               if re.search(r"^\s*pool:\s*" + re.escape(pool) + r"\s*$",
                            section, flags=re.MULTILINE)]
    require(len(matches) == 1, "FORCE_IMPORT_POOL_NOT_UNIQUE")
    section = matches[0]
    require(re.search(r"^\s*id:\s*\d+\s*$", section,
                      flags=re.MULTILINE) is not None,
            "FORCE_IMPORT_GUID_NOT_VISIBLE")
    require("The pool was last accessed by another system." in section,
            "FORCE_IMPORT_REQUIRES_PREVIOUS_HOSTID_WARNING")
    require(re.search(r"^\s*state:\s*ONLINE\s*$", section,
                      flags=re.MULTILINE) is not None,
            "FORCE_IMPORT_POOL_NOT_ONLINE")
    leaves = re.findall(
        r"^\s*(wwn-0x[0-9a-f]{16}(?:-part[0-9]+)?)\s+"
        r"(?:ONLINE|DEGRADED|FAULTED|UNAVAIL|OFFLINE)\b",
        section, flags=re.MULTILINE | re.IGNORECASE)
    require(len(leaves) == 1 and leaves[0].lower() == Path(disk).name.lower(),
            "FORCE_IMPORT_VDEV_NOT_EXACT_DECLARED_WWN")


def ensure_import(pool, disk, physical, access):
    guid = get_pool(pool)
    if guid:
        verify_pool_disk(pool, physical)
        return guid, False
    require(imported_guid_visible(pool) is not None, "REQUESTED_POOL_NOT_FOUND")
    args = ["/usr/sbin/zpool", "import", "-N", "-d", "/dev/disk/by-id"]
    if access == "readonly":
        args += ["-o", "readonly=on"]
    args.append(pool)
    if command(*args, allow_failure=True) is None:
        allow_force_import_of_sole_verified_vdev(pool, disk)
        command(*args[:-1], "-f", pool)
        print("POOL=FORCE_IMPORTED_AFTER_VERIFIED_VM_REPLACEMENT", flush=True)
    guid = get_pool(pool)
    require(guid is not None, "POOL_IMPORT_UNCONFIRMED")
    verify_pool_disk(pool, physical)
    return guid, True


def physical_partitions(physical):
    """Read verified disk's partition tree; never guess from flat lsblk JSON.

    Util-linux lsblk -J without --tree returns sibling disk/partition
    records when NAME is not among selected columns. Force --tree and reject
    any ambiguous/unexpected topology before modifying a disk.
    """
    topology = json.loads(command("/usr/bin/lsblk", "-J", "-T", "-o",
                                  "PATH,PARTUUID,TYPE", physical))
    roots = topology.get("blockdevices")
    require(isinstance(roots, list) and len(roots) == 1,
            "DATA_DISK_PARTITION_TOPOLOGY_UNVERIFIED")
    root = roots[0]
    require(isinstance(root, dict),
            "DATA_DISK_PARTITION_TOPOLOGY_UNVERIFIED")
    children = root.get("children", [])
    require(isinstance(children, list),
            "DATA_DISK_PARTITION_TOPOLOGY_UNVERIFIED")
    if root.get("type") != "disk" or root.get("path") != physical:
        # Unknown partitions on an incompletely described disk are still
        # UNKNOWN_EXISTING_PARTITIONS, never treated as an empty disk.
        require(not children, "UNKNOWN_EXISTING_PARTITIONS")
        raise Blocked("DATA_DISK_PARTITION_TOPOLOGY_UNVERIFIED")
    for part in children:
        require(isinstance(part, dict)
                and part.get("type") == "part"
                and isinstance(part.get("path"), str)
                and part["path"].startswith("/dev/"),
                "DATA_DISK_PARTITION_TOPOLOGY_UNVERIFIED")
    return children


def ensure_create(data, disk, physical, disk_policy="preserve"):
    """Rebuild an empty or exactly identified approved pool (erase only at CLI)."""
    require(disk_policy in ("preserve", "erase"), "DISK_POLICY_INVALID")
    name = data["pool_name"]
    target_active = get_pool(name)
    target_importable = imported_guid_visible(name) if not target_active else None
    if target_active or target_importable:
        guid, modified = ensure_import(name, disk, physical, data["mode"])
        if disk_policy == "preserve":
            print("POOL=ALREADY_CANONICAL", flush=True)
            return guid, modified
        require(data.get("destructive_without_backup") is True,
                "DESTRUCTIVE_APPROVAL_MISSING")
        verify_pool_disk(name, physical)
        command("/usr/sbin/zpool", "destroy", name)
        require(get_pool(name) is None, "CANONICAL_POOL_STILL_ACTIVE")
        print("CANONICAL_POOL=DESTROYED_EXPLICITLY", flush=True)
        # The previously verified ZFS layout may have partitioned the SSD.
        # Continue only with the same strict WWN-verified disk path.
    else:
        legacy = data.get("legacy_pool")
        legacy_active = get_pool(legacy["name"]) if legacy else None
        legacy_importable = imported_guid_visible(legacy["name"]) if legacy else None
        if legacy_active or legacy_importable:
            require(disk_policy == "erase", "LEGACY_POOL_ERASE_NOT_AUTHORIZED")
            require(data.get("destructive_without_backup") is True,
                    "DESTRUCTIVE_APPROVAL_MISSING")
            observed = legacy_active or legacy_importable
            require(observed == str(legacy["guid"]), "LEGACY_POOL_GUID_MISMATCH")
            children = physical_partitions(physical)
            require(len(children) == 1
                    and isinstance(children[0].get("partuuid"), str)
                    and children[0]["partuuid"].lower()
                    == legacy["partition_partuuid"].lower(), "LEGACY_PARTUUID_MISMATCH")
            if not legacy_active:
                command("/usr/sbin/zpool", "import", "-N", "-f", "-d",
                        "/dev/disk/by-id", legacy["name"])
            require(get_pool(legacy["name"]) == str(legacy["guid"]),
                    "LEGACY_POOL_IMPORTED_GUID_DRIFT")
            verify_pool_disk(legacy["name"], physical)
            command("/usr/sbin/zpool", "destroy", legacy["name"])
            print("LEGACY_POOL=DESTROYED_EXPLICITLY", flush=True)
        else:
            # Never interpret arbitrary partitions as erase approval.
            children = physical_partitions(physical)
            require(not children, "UNKNOWN_EXISTING_PARTITIONS")
            require(command("/usr/sbin/blkid", physical, allow_failure=True) is None,
                    "UNKNOWN_EXISTING_SIGNATURE")
            print("DATA_DISK=EMPTY", flush=True)
    # The physical disk was previously identified from serial+WWN+size.
    command("/usr/sbin/wipefs", "--all", physical)
    command("/usr/sbin/sgdisk", "--zap-all", physical)
    command("/usr/bin/udevadm", "settle")
    legacy = data.get("legacy_pool")
    if legacy:
        require(not get_pool(legacy["name"]), "LEGACY_POOL_STILL_ACTIVE")
    command("/usr/sbin/zpool", "create", "-f", "-o", "ashift=12",
            "-O", "compression=zstd", "-O", "atime=off",
            "-O", "xattr=sa", "-O", "acltype=posixacl",
            name, disk)
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
    parser.add_argument("--action", choices=("import", "erase"), required=True)
    args = parser.parse_args()
    require(os.geteuid() == 0, "ROOT_REQUIRED")
    require(args.config.is_file() and not args.config.is_symlink(), "CONFIG_MISSING")
    data = json.loads(args.config.read_text())
    require(re.fullmatch("[a-z][a-z0-9-]{3,60}-pool", data["pool_name"]) is not None,
            "POOL_NAMING_INVALID")
    require(data["media_dataset"] == data["pool_name"] + "/media",
            "CANONICAL_MEDIA_DATASET_MISMATCH")
    validate_layout(data)
    require(args.action != "erase" or data["mode"] == "readwrite",
            "ERASE_REQUIRES_READWRITE")
    require(args.action != "erase"
            or data.get("destructive_without_backup") is True,
            "DISK_ERASE_NOT_APPROVED_IN_FLEET")
    disk, physical = find_disk(data["disk"])
    command("/usr/sbin/modprobe", "zfs")
    if args.action == "erase":
        guid, changed = ensure_create(data, disk, physical, "erase")
    else:
        guid, changed = ensure_import(data["pool_name"], disk, physical, data["mode"])
        print("POOL=IMPORTED", flush=True)
    expected_access = "on" if data["mode"] == "readonly" else "off"
    require(command("/usr/sbin/zpool", "get", "-H", "-o", "value",
                    "readonly", data["pool_name"]) == expected_access,
            "POOL_ACCESS_MODE_MISMATCH")
    require(reconcile_layout(data) is not None, "STORAGE_LAYOUT_ERROR")
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
