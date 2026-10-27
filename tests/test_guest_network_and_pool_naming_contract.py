"""Offline contracts: shared Bonjour baseline and owner-only ZFS creation naming."""
from pathlib import Path
import importlib.util
import json
import os
import struct
import tempfile
from unittest import mock

import yaml

ROOT = Path(__file__).resolve().parents[1]


def module(file, name):
    spec = importlib.util.spec_from_file_location(name, ROOT / file)
    obj = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obj)
    return obj


def test_multicast_parser_reads_actual_guest_ipv4_not_router_dns():
    parser = module("roles/guest_network_discovery/files/dh-mdns-probe.py",
                    "mdns_probe_test")
    q = bytes([len("mediaserver-serzhan")]) + b"mediaserver-serzhan\x05local\x00"
    query = struct.pack("!HHHHHH", 0, 0x8400, 1, 1, 0, 0)
    query += q + struct.pack("!HH", 1, 1)
    answer = b"\xc0\x0c" + struct.pack("!HHIH", 1, 1, 120, 4)
    assert parser.records(query + answer + bytes([192, 168, 11, 238])) == [
        ("mediaserver-serzhan.local", "192.168.11.238")]
    assert parser.records(b"\x00" * 12) == []
    try:
        parser.name_at(b"\xc0\x00", 0)
    except ValueError:
        pass
    else:
        assert False, "DNS pointer loop must be rejected"


def test_guarded_shared_discovery_integrated_without_second_media_daemon():
    role = yaml.safe_load((ROOT / "roles/guest_network_discovery/tasks/main.yml").read_text())
    names = [task["name"] for task in role]
    assert "Require approved LAN mDNS guest contract" in names
    assert "Query actual mDNS A record from independent LAN node" in names
    assert "Enable Avahi without unnecessary restart" in names
    ensure = role[names.index("Enable Avahi without unnecessary restart")]
    assert ensure["ansible.builtin.systemd_service"]["state"] == "started"
    assert ensure["ansible.builtin.systemd_service"]["enabled"] is True
    probe = role[names.index("Query actual mDNS A record from independent LAN node")]
    assert probe["delegate_to"] == "{{ guest_network_discovery_delegate }}"
    assert probe["changed_when"] is False
    assert "dh-mdns-probe.py" in probe["ansible.builtin.script"]["cmd"]
    media = yaml.safe_load((ROOT / "roles/media_guest_setup/tasks/main.yml").read_text())
    assert any(task.get("ansible.builtin.import_role", {}).get("name") ==
               "guest_network_discovery" for task in media)
    discovery = yaml.safe_load(
        (ROOT / "roles/media_guest_setup/tasks/discovery_portal.yml").read_text())
    assert any("nginx-light" in str(x) for x in discovery)
    assert not any("avahi-daemon" in str(x) for x in discovery)
    net = yaml.safe_load((ROOT / "roles/netalertx_native/tasks/main.yml").read_text())
    assert any(task.get("ansible.builtin.import_role", {}).get("name") ==
               "guest_network_discovery" for task in net)
    play = yaml.safe_load((ROOT / "playbooks/deploy-netalertx-pve-from-worker.yml").read_text())
    assert "netalertx_native_ipv4_cidr" in str(play)


def test_legacy_import_keeps_old_name_and_erase_requires_owner_pool():
    storage = module("roles/media_guest_setup/files/dh-media-storage.py",
                     "media_storage_naming_test")
    with tempfile.TemporaryDirectory() as folder:
        config = Path(folder) / "config.json"
        data = {
            "pool_name": "serzhan-home-pool",
            "media_dataset": "serzhan-home-pool/media",
            "mode": "readwrite",
            "layout": {"datasets": ["media"], "directories": []},
            "destructive_without_backup": True,
            "disk": {"wwn": "0x1111222233334444", "serial": "TESTDISK0001", "size_bytes": 1000000},
        }
        config.write_text(json.dumps(data))
        with mock.patch.object(storage.os, "geteuid", return_value=0), \
             mock.patch.object(storage, "find_disk", side_effect=RuntimeError("DISK_WOULD_BE_READ")):
            with mock.patch.object(storage.sys, "argv", [
                    "storage", "--config", str(config), "--action", "import",
                    "--owner", "serzhan"]):
                try:
                    storage.main()
                except RuntimeError as exc:
                    assert str(exc) == "DISK_WOULD_BE_READ"
                else:
                    assert False, "Legacy import must reach the disk read stage"
            with mock.patch.object(storage.sys, "argv", [
                    "storage", "--config", str(config), "--action", "erase",
                    "--owner", "serzhan"]):
                try:
                    storage.main()
                except storage.Blocked as exc:
                    assert str(exc) == "NEW_POOL_OWNER_NAMING_REQUIRED"
                else:
                    assert False, "Legacy name must be rejected on new pool create"
            data["pool_name"] = "serzhan-pool"
            data["media_dataset"] = "serzhan-pool/media"
            config.write_text(json.dumps(data))
            with mock.patch.object(storage.sys, "argv", [
                    "storage", "--config", str(config), "--action", "erase",
                    "--owner", "serzhan"]):
                try:
                    storage.main()
                except RuntimeError as exc:
                    assert str(exc) == "DISK_WOULD_BE_READ"
                else:
                    assert False, "Canonical owner-pool must proceed to disk identification"


def test_canonical_pool_name_validation_only_applies_to_erase():
    play = yaml.safe_load((ROOT / "playbooks/plan-pve-media-vm.yml").read_text())
    tasks = play[0]["pre_tasks"]
    task = next(x for x in tasks if x["name"] == "Validate canonical storage inventory")
    clauses = task["ansible.builtin.assert"]["that"]
    assert "media_raw_disk_action != 'erase' or media_vm.storage.pool_name == site.owner_slug ~ '-pool'" in clauses
    assert "media_vm.storage.pool_name == site.id ~ '-pool'" not in clauses
    setup = yaml.safe_load((ROOT / "roles/media_guest_setup/tasks/main.yml").read_text())
    zfs = next(x for x in setup if x["name"] ==
               "Reconcile physical ZFS pool and declared datasets")
    assert "--owner" in zfs["ansible.builtin.command"]["argv"]


def test_pinned_installer_stages_reusable_role():
    import ast
    source = (ROOT / "bootstrap/semaphore-host-install.py").read_text()
    tree = ast.parse(source)
    assign = next(x.value for x in tree.body if isinstance(x, ast.Assign) and
                  any(isinstance(y, ast.Name) and y.id == "CODE_FILES" for y in x.targets))
    files = ast.literal_eval(assign)
    for relative in ("roles/guest_network_discovery/tasks/main.yml",
                     "roles/guest_network_discovery/files/dh-mdns-probe.py"):
        assert relative in files
    assert '( "media_guest_setup", "tasks/main.yml")' not in source
    assert '("guest_network_discovery", "tasks/main.yml")' in source


def test_fresh_runner_ct_uses_same_pinned_bonjour_role_without_retrofit():
    installer = (ROOT / "bootstrap/semaphore-host-install.py").read_text()
    playbook = (ROOT / "playbooks/commission-new-runner-bonjour.yml").read_text()
    assert '"playbooks/commission-new-runner-bonjour.yml"' in installer
    assert 'def commission_new_runner_mdns(' in installer
    assert 'if not (args.resume or args.resume_ansible or args.resume_semaphore):' in installer
    assert 'commission_new_runner_mdns(args.site, args.runner_cidr, args.pve_ip)' in installer
    assert 'NEW_RUNNER_BONJOUR=VERIFIED' in installer
    plays = yaml.safe_load(playbook)
    assert plays[0]["hosts"] == "localhost"
    assert plays[0]["roles"][0]["role"] == "guest_network_discovery"
    assert "StrictHostKeyChecking=yes" in playbook
    assert "/var/lib/ansible/.ssh/pve_host_audit_known_hosts" in playbook
    assert "dh_runner_mdns_first_boot" in playbook
    assert "NEW_RUNNER_BONJOUR=VERIFIED" in playbook
