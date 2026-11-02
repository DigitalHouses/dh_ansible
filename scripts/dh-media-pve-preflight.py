#!/usr/bin/env python3
"""Read-only PVE physical disk/VM identity check before native media VM apply.

This is a short-lived Ansible script, NOT a daemon or privileged worker. The
site-scoped inventory is read directly from the commissioned CT1231. It must
never erase a filesystem, modify a guest or acquire secrets.
"""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys


class Blocked(Exception):
    pass


def need(ok, name):
    if not ok:
        raise Blocked(name)


def command(*argv):
    result = subprocess.run(argv, text=True, capture_output=True,
                            timeout=25, check=False)
    if result.returncode:
        raise Blocked("PREFLIGHT_COMMAND_FAILED_" + Path(argv[0]).name)
    return result.stdout.strip()


def inventory(site):
    need(os.geteuid() == 0 and Path("/etc/pve/user.cfg").is_file(),
         "PVE_ROOT_REQUIRED")
    need(re.fullmatch(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)+", site),
         "SITE_ID_INVALID")
    need(command("/usr/sbin/pct", "exec", "1231", "--", "hostname")
         == "ansible-" + site, "RUNNER_IDENTITY_DRIFT")
    source = "/var/lib/ansible/site-data/" + site + "/media_vm.yml"
    raw = command("/usr/sbin/pct", "exec", "1231", "--", "cat", source)
    import yaml
    data = yaml.safe_load(raw)["media_vm"]
    return data


def verify_actual_data_disk(data):
    spec = data["storage"]["disk"]
    approval = data["storage"]["one_time_ntfs_erase"]
    need(approval.get("approved") is True
         and approval.get("scope") == "first_create_only"
         and data["storage"].get("destructive_without_backup") is True
         and spec["serial"] == approval["serial"]
         and spec["wwn"] == approval["wwn"]
         and spec["size_bytes"] == approval["size_bytes"]
         and approval["partition_filesystem"] == "ntfs"
         and approval["partition_label"] == "HITACHI_4TB"
         and approval["partition_partuuid"] == spec["observed_partuuid"]
         and approval["reserved_partuuid"] == spec["observed_reserved_partuuid"],
         "DISK_APPROVAL_NOT_EXACT")
    link = Path("/dev/disk/by-id/wwn-" + spec["wwn"])
    need(link.is_symlink(), "DISK_WWN_NOT_ATTACHED")
    disk = link.resolve(strict=True)
    need(disk.name.startswith("sd") and disk.is_block_device(),
         "DISK_DEVICE_UNEXPECTED")
    info = json.loads(command("/usr/bin/lsblk", "-J", "-b", "-o",
                              "PATH,TYPE,MODEL,SERIAL,WWN,SIZE,FSTYPE,LABEL,PARTUUID,MOUNTPOINTS",
                              str(disk)))
    devices = info.get("blockdevices", [])
    need(len(devices) == 1, "DISK_TOPOLOGY_AMBIGUOUS")
    actual = devices[0]
    need(actual.get("path") == str(disk)
         and actual.get("type") == "disk"
         and str(actual.get("serial") or "") == spec["serial"]
         and str(actual.get("wwn") or "").lower() == spec["wwn"].lower()
         and int(actual.get("size") or 0) == spec["size_bytes"]
         and not any(actual.get("mountpoints") or []),
         "DISK_HARDWARE_IDENTITY_DRIFT")
    need(not actual.get("fstype"), "DATA_DISK_IS_ALREADY_FORMATTED")
    children = actual.get("children", [])
    need(len(children) == 2, "NTFS_PARTITION_LAYOUT_DRIFT")
    first, second = children
    need(first.get("path") == str(disk) + "1"
         and first.get("type") == "part"
         and first.get("partuuid", "").lower() == approval["reserved_partuuid"]
         and not first.get("fstype")
         and int(first.get("size") or 0) == 134217728
         and not any(first.get("mountpoints") or []),
         "NTFS_RESERVED_PARTITION_DRIFT")
    need(second.get("path") == str(disk) + "2"
         and second.get("type") == "part"
         and second.get("partuuid", "").lower() == approval["partition_partuuid"]
         and second.get("fstype") == "ntfs"
         and second.get("label") == "HITACHI_4TB"
         and not any(second.get("mountpoints") or [])
         and not second.get("children"),
         "NTFS_DATA_PARTITION_DRIFT")
    controller = data["pci"]["sata_controller"]
    path = Path("/sys/class/block/" + disk.name + "/device").resolve()
    need("/" + controller + "/" in str(path),
         "DISK_NOT_ON_APPROVED_SATA_CONTROLLER")
    root = json.loads(command("/usr/bin/lsblk", "-J", "-o", "PATH,TYPE,MOUNTPOINTS",
                              "/dev/nvme0n1"))
    need(root.get("blockdevices", [{}])[0].get("path") != str(disk),
         "DISK_APPEARS_SYSTEM_DEVICE")
    return disk.name


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--site", required=True)
    p.add_argument("--vm-action", choices=("create", "keep", "recreate"), required=True)
    p.add_argument("--disk-action", choices=("import", "erase"), required=True)
    args = p.parse_args()
    data = inventory(args.site)
    need(data["name"] == "mediaserver-" + args.site.rsplit("-", 1)[0],
         "MEDIA_VM_NAME_MISMATCH")
    vmid = int(data["vmid"])
    need(2200 <= vmid <= 2254, "MEDIA_VMID_INVALID")
    need(int(data["network"]["ipv4"].split("/")[0].split(".")[-1]) + 2000 == vmid,
         "MEDIA_VMID_IP_MISMATCH")
    existing = Path("/etc/pve/qemu-server/" + str(vmid) + ".conf")
    if args.vm_action == "create":
        need(not existing.exists(), "TARGET_VMID_OCCUPIED")
        need(args.disk_action == "erase", "FIRST_CREATE_REQUIRES_ERASE")
        need(data["approvals"]["provision"]
             and data["approvals"]["gpu_cutover"]
             and data["approvals"]["sata_cutover"],
             "PROVISION_NOT_APPROVED")
        disk = verify_actual_data_disk(data)
        print("MEDIA_PVE_PREFLIGHT=PASS VM_ABSENT DISK_EXACT_NTFS gpu="
              + data["pci"]["gpu"] + " sata=" + data["pci"]["sata_controller"]
              + " device=" + disk)
    else:
        need(args.vm_action == "keep", "NATIVE_RECREATE_NOT_APPROVED")
        need(existing.is_file(), "MEDIA_VM_NOT_FOUND")
        conf = command("/usr/sbin/qm", "config", str(vmid))
        need("name: " + data["name"] in conf
             and "description: DigitalHouses managed media guest v2" in conf
             and "hostpci0: " + data["pci"]["gpu"] + ",pcie=1" in conf
             and "hostpci1: " + data["pci"]["sata_controller"] + ",pcie=1" in conf,
             "MANAGED_MEDIA_VM_IDENTITY_MISMATCH")
        need(args.disk_action == "import"
             and isinstance(data["storage"].get("import_pool_guid"), str),
             "REUSE_REQUIRES_PINNED_POOL_GUID")
        print("MEDIA_PVE_PREFLIGHT=PASS EXISTING_MANAGED_VM")


if __name__ == "__main__":
    try:
        main()
    except (Blocked, KeyError, ValueError, TypeError, OSError,
            subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        print("MEDIA_PVE_PREFLIGHT=BLOCKED:" +
              (str(exc) if isinstance(exc, Blocked) else type(exc).__name__),
              file=sys.stderr)
        sys.exit(2)
