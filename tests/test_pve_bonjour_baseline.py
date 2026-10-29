"""Guard PVE Bonjour host publishing for DH Network App discovery."""
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[1]
TASKS = ROOT / "roles/pve_bonjour_baseline/tasks/main.yml"
HANDLERS = ROOT / "roles/pve_bonjour_baseline/handlers/main.yml"
PLAYBOOK = ROOT / "playbooks/apply-pve-bonjour.yml"


def test_baseline_only_publishes_on_pve_management_bridge():
    tasks = yaml.safe_load(TASKS.read_text())
    rendered = TASKS.read_text()
    assert "allow-interfaces=vmbr0" in rendered
    assert "use-ipv4=yes" in rendered
    assert "use-ipv6=no" in rendered
    assert "publish-addresses=yes" in rendered
    assert "publish-workstation=yes" in rendered
    assert "enable-reflector=no" in rendered
    assert "PVE_BONJOUR_BRIDGE_OR_IDENTITY_DRIFT" in rendered
    assert "PVE_BONJOUR_ANNOUNCEMENT_NOT_VERIFIED" in rendered
    assert "avahi-resolve" in rendered
    assert "avahi-browse" in rendered
    assert "libnss-mdns" not in rendered
    assert "networking.service" not in rendered
    assert "pveproxy.service" not in rendered
    assert "systemctl restart networking" not in rendered
    assert any("flush_handlers" in t.get("ansible.builtin.meta", "") for t in tasks)


def test_bonjour_handler_restarts_only_avahi():
    handlers = yaml.safe_load(HANDLERS.read_text())
    assert len(handlers) == 1
    handler = handlers[0]["ansible.builtin.systemd_service"]
    assert handler["name"] == "avahi-daemon.service"
    assert handler["state"] == "restarted"


def test_playbook_uses_pinned_site_runner_pve_host_identity():
    playbook = yaml.safe_load(PLAYBOOK.read_text())
    assert playbook[0]["hosts"] == "site_runner"
    assert playbook[0]["become"] is False
    add_host = [task["ansible.builtin.add_host"] for task in playbook[0]["tasks"]
                if "ansible.builtin.add_host" in task][0]
    assert add_host["ansible_host"] == "{{ pve.host }}"
    assert add_host["ansible_user"] == "{{ pve_access.host.user }}"
    assert add_host["ansible_ssh_private_key_file"] == "{{ pve_access.host.identity_file }}"
    assert "StrictHostKeyChecking=yes" in add_host["ansible_ssh_common_args"]
    assert playbook[1]["hosts"] == "dh_bonjour_pve_hosts"
    assert playbook[1]["roles"] == ["pve_bonjour_baseline"]
    assert "192.168.11.30" not in PLAYBOOK.read_text()
    assert "192.168.11.30" not in TASKS.read_text()
