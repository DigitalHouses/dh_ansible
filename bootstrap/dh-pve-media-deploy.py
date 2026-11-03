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
    # The customer's protected home automation VM has precedence over media.
    # Do not accept silent defaults, particularly on a fresh recreate.
    need(res.get("cores") == 2 and res.get("memory_mb") == 4096
         and res.get("disk_gb") == 64
         and res.get("cpu_limit") == 1.5
         and res.get("cpu_units") == 50
         and res.get("ballooning") is False
         and res.get("zfs_arc_max_mb") == 768
         and res.get("transcode_cpu_limit") == 1.0,
         "MEDIA_PROTECTED_RESOURCE_POLICY_INVALID")
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
    """Honor explicit site display choice; keep serial0 as a separate console.

    In PVE 8.x, VGA serial0 makes built-in noVNC bridge to the same serial
    port as xterm.js, causing multiple competing qm terminal sessions.
    For a GPU passthrough VM with display: std, noVNC has a separate VGA
    framebuffer, while xterm.js remains available over serial0: socket.
    Do not override site choices based on GPU presence.
    """
    requested = data.get("lifecycle", {}).get("display", "serial0")
    need(requested in ("std", "serial0"), "DISPLAY_INVALID")
    return requested


def ensure_lifecycle(vmid, data):
    """Reconcile requested autostart and console even for a running VM."""
    lifecycle = data.get("lifecycle", {})
    display = media_display(data)
    wanted_boot = "1" if lifecycle.get("onboot", False) else "0"
    current = config(vmid)
    if current.get("onboot", "0") != wanted_boot:
        qm("set", vmid, "--onboot", wanted_boot)
    # Media VM starts first; the Ansible runner CT1231 follows with order=2.
    if current.get("startup", "") != "order=1":
        qm("set", vmid, "--startup", "order=1")
    if current.get("vga", "serial0") != display:
        qm("set", vmid, "--vga", display)
    res = data["resources"]
    wanted = {
        "cores": str(res["cores"]),
        "memory": str(res["memory_mb"]),
        "balloon": "0",
        "cpulimit": str(res["cpu_limit"]),
        "cpuunits": str(res["cpu_units"]),
    }
    for field, value in wanted.items():
        # No implicit VM recreation. A running guest may need a later
        # controlled reboot for changes to its active CPU topology.
        if current.get(field) != value:
            qm("set", vmid, "--" + field, value)
    actual = config(vmid)
    need(all(actual.get(key) == val for key, val in wanted.items()),
         "MEDIA_PVE_RESOURCE_POLICY_NOT_APPLIED")


def qga_cicustom(vmid):
    """Normalize Proxmox's canonical order for cicustom options."""
    raw = config(vmid).get("cicustom")
    if raw is None:
        return None
    parts = [pair.split("=", 1) for pair in raw.split(",")]
    need(len(parts) == 2 and all(len(p) == 2 for p in parts),
         "QGA_CLOUDINIT_FOREIGN_CUSTOM_CONFIG")
    need(len(set(p[0] for p in parts)) == 2,
         "QGA_CLOUDINIT_FOREIGN_CUSTOM_CONFIG")
    return dict(parts)


def qga_cicustom_expected(vmid):
    return {
        "vendor": "local:snippets/dh-media-" + str(vmid) + "-qga-v1.yaml",
        "meta": "local:snippets/dh-media-" + str(vmid) + "-qga-meta-v1.yaml",
    }


def ensure_local_snippets_storage(allow_update=True):
    """Read PVE's real pmxcfs storage config, never invoke nonexistent pvesm config.

    Preserve every already-enabled local content type when adding snippets.
    No VM/disk object is altered by this operation.
    """
    path = Path("/etc/pve/storage.cfg")
    need(path.is_file() and not path.is_symlink(),
         "QGA_CLOUDINIT_STORAGE_CONFIG_MISSING")
    content = path.read_text()
    sections = list(re.finditer(
        r"(?m)^([a-z][a-z0-9]*):[ \t]+([A-Za-z0-9_.-]+)[ \t]*$",
        content))
    matches = []
    for i, section in enumerate(sections):
        if section.group(2) != "local":
            continue
        end = sections[i + 1].start() if i + 1 < len(sections) else len(content)
        matches.append((section.group(1), content[section.end():end]))
    need(len(matches) == 1 and matches[0][0] == "dir",
         "QGA_CLOUDINIT_LOCAL_STORAGE_NOT_DIRECTORY")
    attrs = {}
    for line in matches[0][1].splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        fields = line.split(None, 1)
        need(len(fields) == 2 and fields[0] not in attrs,
             "QGA_CLOUDINIT_LOCAL_STORAGE_MALFORMED")
        attrs[fields[0]] = fields[1]
    need(attrs.get("path") == "/var/lib/vz",
         "QGA_CLOUDINIT_LOCAL_STORAGE_PATH_UNEXPECTED")
    types = attrs.get("content", "")
    need(re.fullmatch(r"[a-z][a-z0-9]*(?:,[a-z][a-z0-9]*)*", types)
         is not None, "QGA_CLOUDINIT_LOCAL_CONTENT_UNKNOWN")
    enabled = set(types.split(","))
    if "snippets" not in enabled:
        need(allow_update, "QGA_CLOUDINIT_SNIPPETS_NOT_PERSISTED")
        run("/usr/sbin/pvesm", "set", "local", "--content",
            ",".join(sorted(enabled | {"snippets"})))
        return ensure_local_snippets_storage(False)
    print("MEDIA_QGA_SNIPPETS_STORAGE=READY", flush=True)


def ensure_qga_cloud_init(vmid, data):
    """Vendor-data installs QGA BEFORE native Ansible ever needs QGA.

    A fixed NoCloud instance-id also permits recovering a fresh managed VM
    which was previously booted without vendor-data. No VM/disk recreation,
    passwords, SSH private keys, or access-control changes are involved.
    """
    ensure_local_snippets_storage()
    root = Path("/var/lib/vz/snippets")
    need(not root.is_symlink(), "QGA_CLOUDINIT_SNIPPET_DIR_UNSAFE")
    root.mkdir(mode=0o750, exist_ok=True)
    need(root.is_dir() and not root.is_symlink(),
         "QGA_CLOUDINIT_SNIPPET_DIR_UNSAFE")
    vendor_name = "dh-media-" + str(vmid) + "-qga-v1.yaml"
    meta_name = "dh-media-" + str(vmid) + "-qga-meta-v1.yaml"
    vendor = ("#cloud-config\n"
              "package_update: true\n"
              "packages:\n"
              "  - qemu-guest-agent\n"
              "runcmd:\n"
              "  - [systemctl, enable, --now, qemu-guest-agent.service]\n")
    meta = ("instance-id: dh-media-" + str(vmid) + "-qga-v1\n"
            "local-hostname: " + data["name"] + "\n")
    for filename, payload in ((vendor_name, vendor), (meta_name, meta)):
        file = root / filename
        need(not file.is_symlink(), "QGA_CLOUDINIT_SNIPPET_SYMLINK")
        if file.exists():
            need(file.is_file() and file.read_text() == payload,
                 "QGA_CLOUDINIT_SNIPPET_DRIFT")
            continue
        fd = os.open(file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    wanted = qga_cicustom_expected(vmid)
    current = qga_cicustom(vmid)
    need(current is None or current == wanted,
         "QGA_CLOUDINIT_FOREIGN_CUSTOM_CONFIG")
    if current is None:
        qm("set", vmid, "--cicustom",
           ",".join(k + "=" + wanted[k] for k in ("vendor", "meta")))
    qm("cloudinit", "update", vmid)
    print("MEDIA_QGA_CLOUDINIT=CONFIGURED", flush=True)


def qga_running(vmid):
    try:
        response = subprocess.run(
            ["/usr/sbin/qm", "guest", "cmd", str(vmid), "ping"],
            capture_output=True, text=True, check=False, timeout=12)
        return response.returncode == 0
    except subprocess.TimeoutExpired:
        return False


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
                 "/var/lib/semaphore/.ssh/dh_pve_admin_ed25519")
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
    ensure_qga_cloud_init(vmid, data)


def guest_exec(vmid, *args):
    """Verified QEMU agent command, without host shell or SSH credentials."""
    result = json.loads(qm("guest", "exec", vmid, "--", *args, timeout=45))
    need(result.get("exited") == 1 and result.get("exitcode") == 0,
         "MEDIA_GUEST_AGENT_PREFLIGHT_FAILED")
    return result.get("out-data", "").strip()


def old_guest_matches(vmid, site, data, *, managed=False):
    """Verify only the exact managed VM before deleting its virtual boot disk.

    The physical SATA disk is invisible on PVE after PCI passthrough. Its
    identity is proved by the guest independently before erase/import.
    """
    state = config(vmid)
    need(managed and state.get("description") == MANAGED_MEDIA_DESCRIPTION,
         "FOREIGN_VM_BLOCKED")
    need(state.get("name") == data["name"], "FOREIGN_VM_BLOCKED")
    need(state.get("scsi0", "").startswith(
        "local-lvm:vm-" + str(vmid) + "-disk-"), "FOREIGN_BOOT_VOLUME")
    need(state.get("hostpci0") == data["pci"]["gpu"] + ",pcie=1",
         "GPU_CONFIG_DRIFT")
    need(state.get("hostpci1") in (data["pci"]["sata_controller"],
                                   data["pci"]["sata_controller"] + ",pcie=1"),
         "SATA_CONFIG_DRIFT")
    need(state.get("lock") is None, "VM_LOCKED")
    for slot in ("scsi1", "scsi2", "sata0", "sata1", "virtio0",
                 "virtio1", "ide0", "ide1", "unused0", "unused1"):
        need(slot not in state, "UNEXPECTED_VM_DISK_" + slot)
    print("MANAGED_VM_IDENTITY=PASS", flush=True)


def staged_guest_matches(vmid, data):
    """Allow continuation of *stopped* partially created Task 80 VM only.

    A failed apply can leave a managed VM with an imported system volume but
    without cloud-init or PCI assignment. Never interpret a foreign VM or
    a RUNNING guest missing a controller as a safe partial deployment.
    """
    state = config(vmid)
    need(qm("status", vmid) == "status: stopped",
         "PARTIAL_MEDIA_VM_MUST_BE_STOPPED")
    need(state.get("name") == data["name"]
         and state.get("description") == MANAGED_MEDIA_DESCRIPTION,
         "PARTIAL_MEDIA_VM_IDENTITY_MISMATCH")
    need(state.get("ostype") == "l26"
         and state.get("machine") == "q35"
         and state.get("bios", "seabios") == "seabios"
         and state.get("net0", "").endswith("bridge=vmbr0")
         and state.get("scsihw") == "virtio-scsi-single",
         "PARTIAL_MEDIA_VM_HARDWARE_MISMATCH")
    for n, expected in (("hostpci0", data["pci"]["gpu"] + ",pcie=1"),
                        ("hostpci1", data["pci"]["sata_controller"] + ",pcie=1")):
        need(n not in state or state[n] == expected,
             "PARTIAL_MEDIA_VM_PCI_MISMATCH_" + n)
    for key in state:
        if re.fullmatch(r"(?:scsi|sata|virtio|ide|unused|hostpci)[0-9]+", key):
            need(key in ("scsi0", "ide2", "unused0", "hostpci0", "hostpci1"),
                 "PARTIAL_MEDIA_VM_UNEXPECTED_DEVICE_" + key)
    prefix = "local-lvm:vm-" + str(vmid) + "-disk-"
    if "scsi0" in state:
        need(state["scsi0"].startswith(prefix), "PARTIAL_MEDIA_VM_FOREIGN_DISK")
        need("unused0" not in state, "PARTIAL_MEDIA_VM_EXTRA_DISK")
    if "unused0" in state:
        need(state["unused0"].startswith(prefix),
             "PARTIAL_MEDIA_VM_FOREIGN_IMPORTED_DISK")
    if "ide2" in state:
        need(state["ide2"].startswith("local-lvm:cloudinit"),
             "PARTIAL_MEDIA_VM_FOREIGN_CLOUDINIT")
    need(qga_cicustom(vmid) in (None, qga_cicustom_expected(vmid)),
         "PARTIAL_MEDIA_VM_FOREIGN_CICUSTOM")
    print("PARTIAL_MEDIA_VM=VERIFIED_STOPPED_RESUMABLE", flush=True)


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
        elif qm("status", vmid) == "status: stopped":
            # A stopped VM must be fully managed or exactly match the narrow
            # partially provisioned VM state left by this same task.
            staged_guest_matches(vmid, data)
        else:
            old_guest_matches(vmid, site, data, managed=(
                state.get("description") == MANAGED_MEDIA_DESCRIPTION))
        print("MEDIA_VM=EXISTS_PRESERVED", flush=True)
        return
    managed = state.get("description") == MANAGED_MEDIA_DESCRIPTION
    old_guest_matches(vmid, site, data, managed=managed)
    # The VM itself serves Proxmox's NFS backup storage. Refuse to remove it
    # during a running vzdump job. The job schedule is left untouched.
    active_pve_tasks = json.loads(run("/usr/bin/pvesh", "get",
                                       "/nodes/" + run("/usr/bin/hostname", "-s") + "/tasks",
                                       "--source", "active",
                                       "--output-format", "json"))
    need(isinstance(active_pve_tasks, list), "PVE_ACTIVE_TASKS_INVALID")
    need(not any(t.get("type") in ("vzdump", "backup")
                 for t in active_pve_tasks if isinstance(t, dict)),
         "ACTIVE_PVE_BACKUP_BLOCKS_MEDIA_RECREATE")
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
            if qga_running(vmid):
                print("MEDIA_QGA=READY_ALREADY_RUNNING", flush=True)
                print("MEDIA_VM=ALREADY_RUNNING", flush=True)
                return
            # Resume the just-created VM from an interrupted Task 80.
            # Only a VM commissioned by DigitalHouses with the canonical
            # boot volume and expected hostpci assignments may be restarted.
            old_guest_matches(vmid, site, data, managed=(
                config(vmid).get("description") == MANAGED_MEDIA_DESCRIPTION))
            current_custom = qga_cicustom(vmid)
            need(current_custom is None
                 or current_custom == qga_cicustom_expected(vmid),
                 "QGA_CLOUDINIT_FOREIGN_CUSTOM_CONFIG")
            if current_custom is not None:
                # An earlier run already queued cloud-init bootstrap. Do not
                # disrupt an in-progress apt install with another reboot.
                print("MEDIA_QGA=BOOTSTRAP_ALREADY_CONFIGURED", flush=True)
                print("MEDIA_VM=EXISTS_PRESERVED", flush=True)
                return
            ensure_qga_cloud_init(vmid, data)
            qm("shutdown", vmid, "--timeout", "120", timeout=135)
            need(qm("status", vmid) == "status: stopped",
                 "QGA_RECOVERY_ACPI_SHUTDOWN_FAILED")
            qm("start", vmid, timeout=120)
            need(qm("status", vmid) == "status: running",
                 "QGA_RECOVERY_RESTART_FAILED")
            print("MEDIA_QGA=RECOVERY_CLOUDINIT_RESTARTED", flush=True)
            print("MEDIA_VM=EXISTS_PRESERVED", flush=True)
            return
    print("TARGET_VMID=" + str(vmid), flush=True)
    print("GUEST_IP=" + ip, flush=True)
    image, _ = download_verified() if not existing or "scsi0" not in config(vmid) else (None, None)
    if not existing:
        qm("create", vmid, "--name", name,
           "--ostype", "l26", "--machine", "q35", "--bios", "seabios",
           "--cores", str(data["resources"]["cores"]),
           "--memory", str(data["resources"]["memory_mb"]),
           "--balloon", "0",
           "--cpulimit", str(data["resources"]["cpu_limit"]),
           "--cpuunits", str(data["resources"]["cpu_units"]),
           "--scsihw", "virtio-scsi-single",
           "--net0", "virtio,bridge=vmbr0", "--serial0", "socket",
           "--vga", media_display(data),
           "--onboot", "1" if data.get("lifecycle", {}).get("onboot") else "0",
           "--startup", "order=1",
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
