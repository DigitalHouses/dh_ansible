#!/usr/bin/env python3
"""Read-only PVE/VM/SATA-controller ownership gate for Task 80.

Physical disks behind a passed-through controller are invisible to the PVE
host. Their presence and sole-disk identity are checked inside the media VM,
after hostpci attachment, by dh-media-storage.py.
"""
import argparse
import os
from pathlib import Path
import re
import subprocess
import sys

import yaml


class Blocked(Exception):
    pass


def need(ok, reason):
    if not ok:
        raise Blocked(reason)


def command(*argv):
    p = subprocess.run(argv, text=True, capture_output=True,
                       timeout=25, check=False)
    need(p.returncode == 0, "PREFLIGHT_COMMAND_FAILED_" + Path(argv[0]).name)
    return p.stdout.strip()


def inventory(site):
    need(os.geteuid() == 0 and Path("/etc/pve/user.cfg").is_file(),
         "PVE_ROOT_REQUIRED")
    need(re.fullmatch(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)+", site),
         "SITE_ID_INVALID")
    need(command("/usr/sbin/pct", "exec", "1231", "--", "hostname")
         == "ansible-" + site, "RUNNER_IDENTITY_DRIFT")
    path = "/var/lib/ansible/site-data/" + site + "/media_vm.yml"
    return yaml.safe_load(command("/usr/sbin/pct", "exec", "1231", "--",
                                  "cat", path))["media_vm"]


def verify_controller(data):
    bdf = data["pci"]["sata_controller"]
    need(re.fullmatch(r"[0-9a-f]{4}:[0-9a-f]{2}:[0-9a-f]{2}\.[0-7]", bdf),
         "SATA_CONTROLLER_ADDRESS_INVALID")
    pci = Path("/sys/bus/pci/devices") / bdf
    need(pci.is_dir(), "SATA_CONTROLLER_NOT_ATTACHED")
    need((pci / "class").read_text().strip().lower().startswith("0x0106"),
         "SATA_CONTROLLER_NOT_AHCI")
    for field in ("vendor", "device"):
        need(re.fullmatch(r"0x[0-9a-f]{4}",
                          (pci / field).read_text().strip().lower()),
             "SATA_CONTROLLER_PCI_ID_INVALID")
    return bdf


def verify_vm(data, vm_action):
    vmid = int(data["vmid"])
    need(2200 <= vmid <= 2254, "MEDIA_VMID_INVALID")
    need(int(data["network"]["ipv4"].split("/")[0].split(".")[-1]) + 2000
         == vmid, "MEDIA_VMID_IP_MISMATCH")
    cfg = Path("/etc/pve/qemu-server") / (str(vmid) + ".conf")
    if vm_action == "create":
        need(not cfg.exists(), "TARGET_VMID_OCCUPIED")
        return "VM_ABSENT"
    need(cfg.is_file(), "MEDIA_VM_NOT_FOUND")
    lines = command("/usr/sbin/qm", "config", str(vmid)).splitlines()
    state = dict(line.split(": ", 1) for line in lines if ": " in line)
    need(state.get("name") == data["name"]
         and state.get("description") == "DigitalHouses managed media guest v2",
         "MANAGED_MEDIA_VM_IDENTITY_MISMATCH")
    need(state.get("lock") is None, "PARTIAL_MEDIA_VM_LOCKED")
    prefix = "local-lvm:vm-" + str(vmid) + "-disk-"
    fully_configured = (
        state.get("hostpci0") == data["pci"]["gpu"] + ",pcie=1"
        and state.get("hostpci1") in (
            data["pci"]["sata_controller"],
            data["pci"]["sata_controller"] + ",pcie=1")
        and state.get("scsi0", "").startswith(prefix))
    if fully_configured:
        return "MANAGED_VM"
    need(vm_action == "keep", "MANAGED_MEDIA_VM_IDENTITY_MISMATCH")
    need(command("/usr/sbin/qm", "status", str(vmid)) == "status: stopped",
         "PARTIAL_MEDIA_VM_MUST_BE_STOPPED")
    need(state.get("ostype") == "l26"
         and state.get("machine") == "q35"
         and state.get("bios", "seabios") == "seabios"
         and state.get("scsihw") == "virtio-scsi-single"
         and state.get("net0", "").endswith("bridge=vmbr0"),
         "PARTIAL_MEDIA_VM_HARDWARE_MISMATCH")
    for key in state:
        if re.fullmatch(r"(?:scsi|sata|virtio|ide|unused|hostpci)[0-9]+", key):
            need(key in ("scsi0", "ide2", "unused0", "hostpci0", "hostpci1"),
                 "PARTIAL_MEDIA_VM_UNEXPECTED_DEVICE")
    need(state.get("hostpci0") in (None, data["pci"]["gpu"] + ",pcie=1")
         and state.get("hostpci1") in (
             None, data["pci"]["sata_controller"] + ",pcie=1"),
         "PARTIAL_MEDIA_VM_PCI_MISMATCH")
    need("scsi0" not in state or state["scsi0"].startswith(prefix),
         "PARTIAL_MEDIA_VM_FOREIGN_DISK")
    need("unused0" not in state or state["unused0"].startswith(prefix),
         "PARTIAL_MEDIA_VM_FOREIGN_IMPORTED_DISK")
    need(not ("scsi0" in state and "unused0" in state),
         "PARTIAL_MEDIA_VM_EXTRA_DISK")
    need("ide2" not in state or
         state["ide2"].startswith("local-lvm:cloudinit"),
         "PARTIAL_MEDIA_VM_FOREIGN_CLOUDINIT")
    need("cicustom" not in state,
         "PARTIAL_MEDIA_VM_CICUSTOM_NOT_YET_VERIFIED")
    return "MANAGED_VM_STAGED_RESUMABLE"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--site", required=True)
    parser.add_argument("--vm-action", choices=("create", "keep", "recreate"),
                        required=True)
    parser.add_argument("--disk-action", choices=("import", "erase"),
                        required=True)
    args = parser.parse_args()
    data = inventory(args.site)
    need(data["name"] == "mediaserver-" + args.site.rsplit("-", 1)[0],
         "MEDIA_VM_NAME_MISMATCH")
    need(data["approvals"]["provision"]
         and data["approvals"]["sata_cutover"],
         "MEDIA_VM_PROVISION_NOT_APPROVED")
    bdf = verify_controller(data)
    vm = verify_vm(data, args.vm_action)
    print("MEDIA_PVE_PREFLIGHT=PASS " + vm + " SATA_CONTROLLER=" + bdf,
          flush=True)


if __name__ == "__main__":
    try:
        main()
    except (Blocked, KeyError, ValueError, TypeError, OSError,
            subprocess.TimeoutExpired) as exc:
        print("MEDIA_PVE_PREFLIGHT=BLOCKED:" +
              (str(exc) if isinstance(exc, Blocked) else type(exc).__name__),
              file=sys.stderr)
        sys.exit(2)
