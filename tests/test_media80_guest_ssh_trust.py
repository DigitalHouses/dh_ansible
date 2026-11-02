"""Task 80 must repair host-key trust after VM recreation before SSH provisioning.

The trusted ED25519 public key is obtained through authenticated PVE QGA,
verified against the actual network SSH endpoint, then installed with
semaphore:semaphore/0600. No auto-accept or ignore-host-key bypass.
"""
from pathlib import Path
import yaml


ROOT = Path(__file__).resolve().parents[1]
PLAY = ROOT / "playbooks/plan-pve-media-vm.yml"
PATH = "/var/lib/semaphore/.ssh/dh_media_guest_known_hosts"


def task_by_name(tasks, name):
    return next((t for t in tasks if t["name"] == name), None)


def test_qga_host_key_obtained_after_pve_guest_is_confirmed_running():
    plays = yaml.safe_load(PLAY.read_text())
    assert len(plays) == 4
    pve = plays[1]["tasks"]
    names = [t["name"] for t in pve]
    running = names.index("Refuse to configure an absent, stopped, or foreign VM")
    read = names.index(
        "Read new Media guest ED25519 host key over commissioned PVE QEMU Guest Agent"
    )
    trusted = names.index(
        "Require a single valid Media guest ED25519 host key before network verification"
    )
    assert running < read < trusted
    read_task = pve[read]
    assert read_task["ansible.builtin.command"]["argv"][-2:] == [
        "/bin/cat", "/etc/ssh/ssh_host_ed25519_key.pub"
    ]
    assert read_task["changed_when"] is False
    assert "media_guest_hostkey_qga" in read_task["until"]


def test_network_comparison_before_trust_file_update():
    plays = yaml.safe_load(PLAY.read_text())
    runner = plays[2]["tasks"]
    names = [t["name"] for t in runner]
    network = names.index(
        "Independently read advertised ED25519 key from Media guest SSH endpoint"
    )
    compare = names.index(
        "Verify SSH network identity equals the key read from authenticated QGA"
    )
    write = names.index(
        "Atomically pin the newly recreated guest key with correct semaphore ownership"
    )
    validate = names.index(
        "Verify SSH trust file owner, group, type, and mode"
    )
    connect = names.index(
        "Confirm connection as semaphore with strict SSH identity checking"
    )
    register = names.index(
        "Register newly commissioned Debian media VM using pinned runner SSH"
    )
    assert network < compare < write < validate < connect < register
    assert runner[network]["ansible.builtin.command"]["argv"][0] == "ssh-keyscan"
    assert "media_pve" in str(runner[compare]["ansible.builtin.assert"]["that"])
    assert runner[write]["become"] is True
    copy = runner[write]["ansible.builtin.copy"]
    assert copy["dest"] == PATH
    assert copy["owner"] == copy["group"] == "semaphore"
    assert copy["mode"] == "0600"
    assert copy["follow"] is False
    assert "hostvars['media_pve'].media_verified_ed25519_key" in copy["content"]
    assert "StrictHostKeyChecking=yes" in runner[connect]["ansible.builtin.command"]["argv"]
    assert "UserKnownHostsFile=" + PATH in runner[connect]["ansible.builtin.command"]["argv"]


def test_no_permissive_guest_ssh_hostkey_policy():
    source = PLAY.read_text()
    assert "StrictHostKeyChecking=accept-new" not in source
    assert "StrictHostKeyChecking=no" not in source
    assert "StrictHostKeyChecking=yes" in source
    plays = yaml.safe_load(source)
    guest = task_by_name(
        plays[2]["tasks"],
        "Register newly commissioned Debian media VM using pinned runner SSH",
    )
    assert guest
    args = guest["ansible.builtin.add_host"]["ansible_ssh_common_args"]
    assert "StrictHostKeyChecking=yes" in args
    assert "dh_media_guest_known_hosts" in args
    assert "ssh-keyscan" not in source.split(
        "Atomically pin the newly recreated guest key"
    )[1].split("Register newly commissioned Debian media VM")[0]


def test_keyscan_banners_and_duplicate_keys_are_not_conflicting_host_keys():
    """Keyscan emits comment banners; they cannot be counted as host keys."""
    import re

    plays = yaml.safe_load(PLAY.read_text())
    runner = plays[2]["tasks"]
    names = [t["name"] for t in runner]
    extract_index = names.index(
        "Extract exact guest ED25519 records ignoring scanner banners and duplicates"
    )
    verify_index = names.index(
        "Verify SSH network identity equals the key read from authenticated QGA"
    )
    assert extract_index < verify_index
    extractor = runner[extract_index]["ansible.builtin.set_fact"]["media_ssh_scanned_host_keys"]
    assert "select('match'" in extractor
    assert "regex_escape" in extractor
    assert "ssh-ed25519 " in extractor
    assert "| unique" in extractor
    verified = runner[verify_index]["ansible.builtin.assert"]["that"]
    assert "media_ssh_scanned_host_keys ==" in str(verified)
    assert "stdout_lines | length == 1" not in str(verified)

    ip = "192.168.11.232"
    good = ip + " ssh-ed25519 AAAA"
    banner = "# 192.168.11.232:22 SSH-2.0-OpenSSH_9.2"
    records = [banner, good, good]
    def exact_keys(records):
        return list(dict.fromkeys(
            s for s in records if re.match(r"^" + re.escape(ip) + r" ssh-ed25519 ", s)
        ))

    assert exact_keys(records) == [good]
    assert exact_keys([banner, good, ip + " ssh-ed25519 BBBB"]) != [good]
    assert exact_keys([banner]) != [good]
