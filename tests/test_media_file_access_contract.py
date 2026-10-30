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
    assert len(guard) == 4
    assert guard[-1]['name'] == 'Require complete media v3 site declaration before PVE worker'
    assert guard[-1]['no_log'] is True
    assert 'MEDIA_V3_FLEET_INCOMPLETE' in str(guard[-1])
    content = str(guard)
    assert "serzhan_admin" in content and "administrator" in content
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
    assert pwd["ansible.builtin.command"]["argv"][-4] == "--account"
    assert "selectattr" in pwd["ansible.builtin.command"]["argv"][-3]
    assert pwd["ansible.builtin.command"]["argv"][-2:] == ["--account", "serzhan_admin"]
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
    assert smb.count("map(attribute='name') | first }} serzhan_admin") == 2
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
    baseline = yaml.safe_load((ROOT / "roles/guest_network_discovery/tasks/main.yml").read_text())
    assert any("avahi-daemon" in str(task) for task in baseline)
    assert any(task.get("ansible.builtin.import_role", {}).get("name") ==
               "guest_network_discovery" for task in tasks)
    assert any("wsdd2.service" in str(task) for task in discovery)
    page = (ROLE / "templates/media-portal-index.html.j2").read_text()
    assert "media_vm.portal.links" in page
    # The root-only CLI command contains the word "password", but no credential value.
    assert "password=" not in page.lower()
    assert "secret=" not in page.lower()
    assert 'value="/usr/local/sbin/dh-media-password-show"' in page
    assert ":32400/web" in page


def test_plex_agent_pinned_release_and_mqtt_is_fleet_only():
    tasks = yaml.safe_load((ROLE / "tasks/plex_agent.yml").read_text())
    text = str(tasks)
    assert "digitalhouses_plex_agent-v0.8.5" in text
    assert "MEDIA_PLEX_AGENT_MQTT_FLEET_REQUIRED" in text
    assert "no_log" in text
    conf = (ROLE / "templates/digitalhouses-plex-agent.conf.j2").read_text()
    assert "media_vm.plex_agent.mqtt.password" in conf
    assert "DigitalHouses/Global/digitalhouses_plex_agent" in conf



def test_plex_agent_service_can_traverse_protected_config_directory():
    tasks = yaml.safe_load((ROLE / "tasks/plex_agent.yml").read_text())
    by_name = {task["name"]: task for task in tasks}
    directory = by_name["Create protected Plex Agent configuration directory"]
    assert directory["ansible.builtin.file"] == {
        "path": "/etc/digitalhouses_plex_agent",
        "state": "directory",
        "owner": "root",
        "group": "digitalhouses_plex_agent",
        "mode": "0750",
    }
    assert directory["register"] == "media_plex_agent_directory"
    config = by_name["Seed noninteractive Plex Agent configuration from protected Fleet"]
    assert config["ansible.builtin.template"]["group"] == "digitalhouses_plex_agent"
    assert config["ansible.builtin.template"]["mode"] == "0640"
    for name, filename in [
        ("Check service account can access its protected Plex Agent configuration",
         "digitalhouses_plex_agent.conf"),
        ("Check service account can access its protected Plex API token",
         "plex_local_admin_token"),
    ]:
        check = by_name[name]
        assert check["ansible.builtin.command"]["argv"] == [
            "runuser", "-u", "digitalhouses_plex_agent", "--", "test", "-r",
            "/etc/digitalhouses_plex_agent/" + filename
        ]
        assert check["changed_when"] is False
    start = by_name["Enable active Plex Agent with reconciled site MQTT config"]
    state = start["ansible.builtin.systemd_service"]["state"]
    assert "media_plex_agent_directory.changed" in state
    assert "media_plex_agent_config.changed" in state
    names = list(by_name)
    assert names.index("Check service account can access its protected Plex API token") < names.index(
        "Enable active Plex Agent with reconciled site MQTT config")



def test_pve_legacy_rename_protected_and_non_destructive():
    code = (ROOT / "bootstrap/dh-pve-media-deploy.py").read_text()
    ast.parse(code)
    assert "MEDIA_VM=RENAMED_VERIFIED_LEGACY" in code
    assert "old_guest_matches(vmid, site, data, managed=True)" in code
    assert 'qm("set", vmid, "--name", data["name"])' in code
    assert 'need(name == "mediaserver-" + site.rsplit("-", 1)[0]' in code
    assert "qm(\"destroy\"" in code  # only in explicitly gated recreate path
