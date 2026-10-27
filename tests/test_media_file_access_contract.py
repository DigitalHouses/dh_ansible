"""Offline safety contracts for media owner/admin, SMB, and service portal."""
import ast
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[1]
ROLE = ROOT / "roles/media_guest_setup"


def test_fleet_identity_preflight_precedes_pve_worker():
    play = yaml.safe_load((ROOT / "playbooks/plan-pve-media-vm.yml").read_text())[0]
    names = [task["name"] for task in play["pre_tasks"]]
    assert names.index("Validate Fleet-provided media identities before PVE worker") < names.index("Validate canonical storage inventory")
    guard = yaml.safe_load((ROOT / "playbooks/media-accounts-preflight.yml").read_text())
    assert len(guard) == 3
    content = str(guard)
    assert "serzhan" in content and "serzhan_admin" in content
    assert "item.password is not defined" in content
    assert "MEDIA_FILE_ACCESS_FLEET_REQUIRED" in content


def test_two_privileged_identities_and_protected_credential_store():
    tasks = yaml.safe_load((ROLE / "tasks/file_access.yml").read_text())
    names = [task["name"] for task in tasks]
    assert names.index("Create approved POSIX owners and DigitalHouses admin") < names.index("Generate or reuse protected DigitalHouses credentials and provision Samba")
    create = tasks[names.index("Create approved POSIX owners and DigitalHouses admin")]["ansible.builtin.user"]
    assert create["shell"] == "/usr/sbin/nologin"
    assert create["password_lock"] is True
    assert "media_group,doc_group,backup_group,download_group,admin_group" in create["groups"]
    assert "media_group,doc_group" in create["groups"]
    pwd = tasks[names.index("Generate or reuse protected DigitalHouses credentials and provision Samba")]
    assert pwd["no_log"] is True
    assert pwd["ansible.builtin.command"]["argv"][-4:] == ["--account", "serzhan", "--account", "serzhan_admin"]
    script = (ROLE / "files/dh-media-accounts.py").read_text()
    ast.parse(script)
    assert "secrets.choice(ALPHABET)" in script
    assert "range(8)" in script
    assert "0o600" in script
    assert "MEDIA_CREDENTIALS=READY" in script
    assert "digitalhouses" in script


def test_all_shares_authorized_and_anonymous_access_blocked():
    smb = (ROLE / "templates/media-smb.conf.j2").read_text()
    for share in ("[Media]", "[Documents]", "[DigitalHouses]", "[Backups]"):
        assert share in smb
    assert smb.count("guest ok = no") == 4
    assert smb.count("read only = no") == 4
    assert smb.count("valid users = serzhan serzhan_admin") == 2
    assert smb.count("valid users = serzhan_admin") == 2
    for forbidden in ("guest only = yes", "guest ok = yes", "force user = nobody"):
        assert forbidden not in smb
    tasks = yaml.safe_load((ROLE / "tasks/main.yml").read_text())
    names = [task["name"] for task in tasks]
    assert names.index("Require Fleet identities and provision authenticated SMB access") < names.index("Advertise existing media library over SMB")
    assert "Require anonymous Media access to be denied" in names


def test_portal_and_discovery_are_declared_without_secrets():
    tasks = yaml.safe_load((ROLE / "tasks/main.yml").read_text())
    names = [task["name"] for task in tasks]
    assert "Publish local media portal and LAN discovery" in names
    discovery = yaml.safe_load((ROLE / "tasks/discovery_portal.yml").read_text())
    assert any("avahi-daemon" in str(task) for task in discovery)
    assert any("wsdd2.service" in str(task) for task in discovery)
    page = (ROLE / "templates/media-portal-index.html.j2").read_text()
    assert "media_vm.portal.links" in page
    assert "password" not in page.lower()
    assert ":32400/web" in page


def test_plex_agent_pinned_release_and_mqtt_is_fleet_only():
    tasks = yaml.safe_load((ROLE / "tasks/plex_agent.yml").read_text())
    text = str(tasks)
    assert "digitalhouses_plex_agent-v0.8.0" in text
    assert "MEDIA_PLEX_AGENT_MQTT_FLEET_REQUIRED" in text
    assert "no_log" in text
    conf = (ROLE / "templates/digitalhouses-plex-agent.conf.j2").read_text()
    assert "media_vm.plex_agent.mqtt.password" in conf
    assert "DigitalHouses/Global/digitalhouses_plex_agent" in conf


def test_pve_legacy_rename_protected_and_non_destructive():
    code = (ROOT / "bootstrap/dh-pve-media-deploy.py").read_text()
    ast.parse(code)
    assert "MEDIA_VM=RENAMED_VERIFIED_LEGACY" in code
    assert "old_guest_matches(vmid, site, data, managed=True)" in code
    assert 'qm("set", vmid, "--name", data["name"])' in code
    assert 'need(name == "mediaserver-" + site.rsplit("-", 1)[0]' in code
    assert "qm(\"destroy\"" in code  # only in explicitly gated recreate path
