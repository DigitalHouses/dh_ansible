"""Native media VM provisioning replaces old privileged watcher on fresh sites."""
from pathlib import Path
import ast
import yaml

ROOT = Path(__file__).resolve().parents[1]
PLAY = ROOT / "playbooks/plan-pve-media-vm.yml"
PREFLIGHT = ROOT / "scripts/dh-media-pve-preflight.py"
DEPLOY = ROOT / "bootstrap/dh-pve-media-deploy.py"


def test_native_media80_has_four_phase_site_and_target_flow():
    plays = yaml.safe_load(PLAY.read_text(encoding="utf-8"))
    assert len(plays) == 4
    assert [p["hosts"] for p in plays] == [
        "site_runner", "dh_media_native_pve", "site_runner", "dh_media_native_guest"
    ]
    assert plays[-1]["roles"] == ["media_guest_setup"]
    tasks = plays[0]["tasks"]
    names = [t["name"] for t in tasks]
    assert names.index("Require managed SSH trust before PVE actions") < names.index(
        "Materialize only approved media metadata on the runner")
    assert names.index("Materialize only approved media metadata on the runner") < names.index(
        "Register exactly the commissioned PVE root SSH target")
    assert not any("ansible.builtin.shell" in t for p in plays
                   for t in p.get("tasks", []))
    source = PLAY.read_text(encoding="utf-8")
    for forbidden in ("dh-pve-access-audit.path", "dh-pve-access-audit.request",
                      "dh-pve-media-release-verify", "site_inventory_prepare",
                      "dh-pve-media-worker", "validate_certs: false"):
        assert forbidden not in source
    for required in ("infrastructure_access", "dh_pve_admin_ed25519",
                     "StrictHostKeyChecking=yes", "site-" + "{{ site.id }}",
                     "/contents/.digitalhouses/site.json",
                     "DH_GITHUB_PROVISION_TOKEN", "DH_MQTT_PASSWORD",
                     "media_sata_pci_vendor", "destructive_without_backup",
                     "ansible.builtin.script", "ansible.builtin.wait_for_connection"):
        assert required in source


def test_hardware_preflight_is_in_front_of_deployment():
    steps = yaml.safe_load(PLAY.read_text())[1]["tasks"]
    names = [t["name"] for t in steps]
    assert names.index("Verify PVE/CT/hardware ownership before any VM or disk writes") < names.index(
        "Provision only the declared PVE media VM by native Ansible SSH")
    assert "scripts/dh-media-pve-preflight.py" in str(steps[names.index(
        "Verify PVE/CT/hardware ownership before any VM or disk writes")])
    assert "--apply" in str(steps[names.index(
        "Provision only the declared PVE media VM by native Ansible SSH")])
    source = PREFLIGHT.read_text(encoding="utf-8")
    ast.parse(source)
    for required in ("SATA_CONTROLLER_NOT_ATTACHED",
                     "SATA_CONTROLLER_NOT_AHCI",
                     "MEDIA_VM_PROVISION_NOT_APPROVED",
                     "TARGET_VMID_OCCUPIED",
                     "MANAGED_MEDIA_VM_IDENTITY_MISMATCH"):
        assert required in source
    assert all(cmd not in source for cmd in ('"wipefs"', '"sgdisk"', '"destroy"', '"mkfs"', '"dd"'))


def test_media_deploy_uses_commissioned_native_runner_key():
    code = DEPLOY.read_text(encoding="utf-8")
    ast.parse(code)
    assert '"/var/lib/semaphore/.ssh/dh_pve_admin_ed25519"' in code
    assert 'pct("ssh-keygen", "-y"' in code
    assert '"--apply"' in code
