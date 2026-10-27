"""Media guest role must stay declarative, idempotent and non-destructive."""
from pathlib import Path
import json
import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_existing_task_80_single_button_and_guest_handoff():
    tasks = json.loads((ROOT / "catalog/semaphore-tasks.json").read_text())["tasks"]
    assert len([t for t in tasks if t["title"].startswith("80-Медиасервер")]) == 1
    shell = (ROOT / "bootstrap/pve-access-audit-request.sh").read_text()
    assert "MEDIA_DEPLOY" in shell
    assert 'setup-pve-media-guest.yml' in shell
    assert 'ansible-playbook -i "$INV" "$GUEST_PLAY"' in shell


def test_fixed_guest_playbook_and_role():
    plays = yaml.safe_load((ROOT / "playbooks/setup-pve-media-guest.yml").read_text())
    assert len(plays) == 2
    assert plays[0]["hosts"] == "site_runner"
    assert plays[1]["hosts"] == "dh_media_guest"
    assert "media_guest_setup" in plays[1]["roles"]
    assert "media_vm.yml" in str(plays[0])
    assert "/var/lib/ansible/.ssh/pve_host_write_ed25519" in str(plays[0])


def test_boot_importer_never_formats_disks():
    script = (ROOT / "roles/media_guest_setup/templates/dh-media-zfs-mount.sh.j2").read_text()
    for term in ("WWN=", "SERIAL=", "zpool import",
                 "media_dataset", "MEDIA_ZFS=READY_${ACCESS^^}"):
        assert term in script
    for forbidden in ("zpool create", "zpool destroy", "zpool export",
                      "zfs destroy", "zfs set", "wipefs", "mkfs", "parted",
                      "zfs mount -a"):
        assert forbidden not in script
    unit = (ROOT / "roles/media_guest_setup/files/dh-media-zfs.service").read_text()
    assert "Before=plexmediaserver.service" in unit
    assert "WantedBy=multi-user.target" in unit


def test_official_plex_and_agent_setup_persisted():
    text = (ROOT / "roles/media_guest_setup/tasks/main.yml").read_text()
    yaml.safe_load(text)
    for term in ("qemu-guest-agent", "zfs-dkms", "zfsutils-linux",
                 "https://repo.plex.tv/deb/", "plexmediaserver",
                 "render", "dh-media-zfs.service", "readonly"):
        assert term in text
    installer = (ROOT / "bootstrap/semaphore-host-install.py").read_text()
    for name in ("setup-pve-media-guest.yml", "dh-media-zfs.service",
                 "dh-media-zfs-mount.sh.j2"):
        assert name in installer

def test_plex_libraries_auto_created_without_api_tokens_or_new_task():
    data = yaml.safe_load((ROOT / "roles/media_guest_setup/tasks/main.yml").read_text())
    labels = [task["name"] for task in data]
    assert "Inspect existing Plex library paths without changing its database" in labels
    assert "Create missing Plex libraries using the installed Plex scanner" in labels
    assert "Scan only newly created Plex libraries" in labels
    create = next(x for x in data if x["name"].startswith("Create missing Plex libraries"))
    assert create["ansible.builtin.command"]["argv"][:4] == ["runuser", "-u", "plex", "--"]
    assert len(create["loop"]) == 3
    assert [x["folder"] for x in create["loop"]] == ["movies", "tvshows", "music"]
    assert "not in media_existing_plex_paths.stdout_lines" in create["when"]
    code = (ROOT / "roles/media_guest_setup/tasks/main.yml").read_text()
    assert "firmware-intel-graphics" in code
    assert "intel-media-va-driver" in code
    assert "vainfo" in code

def test_media_smb_discovery_is_readonly_and_idempotent():
    tasks = yaml.safe_load((ROOT / "roles/media_guest_setup/tasks/main.yml").read_text())
    named = {item["name"]: item for item in tasks}
    assert "Advertise existing media library over SMB" in named
    assert "Start Samba and Windows WS-Discovery services" in named
    assert "Confirm readonly guest SMB share is reachable" in named
    assert "samba" in str(tasks) and "wsdd2" in str(tasks)
    template = (ROOT / "roles/media_guest_setup/templates/media-smb.conf.j2").read_text()
    assert "path = /{{ media_vm.storage.media_dataset }}" in template
    assert "read only = yes" in template
    assert "guest ok = yes" in template
    assert template.count("[Media]") == 1
    assert "netbios name = {{ media_vm.discovery.netbios_name }}" in template
    assert "media-smb.conf.j2" in (ROOT / "bootstrap/semaphore-host-install.py").read_text()

def test_native_radarr_sonarr_pinned_without_extra_semaphore_task():
    entries = yaml.safe_load((ROOT / "roles/media_guest_setup/defaults/main.yml").read_text())["media_arr_releases"]
    assert [x["slug"] for x in entries] == ["radarr", "sonarr", "prowlarr"]
    assert [x["port"] for x in entries] == [7878, 8989, 9696]
    assert all(x["source"].startswith("https://github.com/") for x in entries)
    assert all(len(x["sha256"]) == 64 for x in entries)
    yml = (ROOT / "roles/media_guest_setup/tasks/main.yml").read_text()
    for phrase in ("media_vm.applications[item.slug]", "get_url", "checksum:",
                   "unarchive", "systemd_service", "wait_for", "radarr", "sonarr"):
        assert phrase in yml
    template = (ROOT / "roles/media_guest_setup/templates/media-arr.service.j2").read_text()
    assert "ExecStart=/opt/{{ item.name }}/{{ item.name }}" in template
    assert "User={{ item.slug }}" in template
    assert "/var/lib/{{ item.slug }}" in template
    setup = (ROOT / "bootstrap/semaphore-host-install.py").read_text()
    assert '("media_guest_setup", "defaults/main.yml")' in setup
    assert '("media_guest_setup", "templates/media-arr.service.j2")' in setup


def test_guest_smartmontools_is_provisioned_for_dh_pve_agent():
    tasks = yaml.safe_load((ROOT / "roles/media_guest_setup/tasks/main.yml").read_text())
    packages = next(x for x in tasks if x["name"].startswith("Install QEMU Guest Agent"))["ansible.builtin.apt"]["name"]
    assert "smartmontools" in packages
    probe = next(x for x in tasks if x["name"] == "Verify SMART monitoring binary available for dh_pve_agent")
    assert probe["ansible.builtin.command"]["argv"] == ["smartctl", "--version"]
    assert probe["changed_when"] is False


def test_native_qbittorrent_and_staging_only_downloads():
    tasks = yaml.safe_load((ROOT / "roles/media_guest_setup/tasks/main.yml").read_text())
    names = {task["name"] for task in tasks}
    required = {
        "Install Debian qBittorrent headless client",
        "Create qBittorrent native service user",
        "Ensure temporary download directory is not on ZFS",
        "Set staging-only qBittorrent defaults on first installation",
        "Enable and start qBittorrent WebUI",
        "Verify qBittorrent WebUI startup",
    }
    assert required.issubset(names)
    assert "qbittorrent-nox" in str(tasks)
    assert "media_vm.downloads.mode == 'staging-only'" in str(tasks)
    template = (ROOT / "roles/media_guest_setup/templates/media-qbittorrent.conf.j2").read_text()
    assert "Session\\DefaultSavePath={{ media_vm.downloads.path }}/" in template
    assert "Session\\TempPath={{ media_vm.downloads.path }}/incomplete/" in template
    unit = (ROOT / "roles/media_guest_setup/templates/media-qbittorrent.service.j2").read_text()
    assert "User=qbittorrent" in unit
    assert "--webui-port=8080" in unit
    assert "Restart=on-failure" in unit
    setup = (ROOT / "bootstrap/semaphore-host-install.py").read_text()
    assert '("media_guest_setup", "templates/media-qbittorrent.service.j2")' in setup
    assert '("media_guest_setup", "templates/media-qbittorrent.conf.j2")' in setup
    assert len([t for t in json.loads((ROOT / "catalog/semaphore-tasks.json").read_text())["tasks"] if t["title"].startswith("80-Медиасервер")]) == 1


def test_fully_automatic_arr_wiring_without_exposing_api_keys():
    from pathlib import Path
    import ast
    path = ROOT / "roles/media_guest_setup/files/dh-media-arr-wire.py"
    ast.parse(path.read_text())
    code = path.read_text()
    for phrase in ("X-Api-Key", "applications/schema", "downloadclient/schema",
                   "rootfolder", "downloadclient/test", "applications/test",
                   "torrents/createCategory", "ZFS_LIBRARY=UNTOUCHED"):
        assert phrase in code
    assert "print(key)" not in code
    assert "print(api_key" not in code
    assert "os.environ.get('API_KEY')" not in code
    role = yaml.safe_load((ROOT / "roles/media_guest_setup/tasks/main.yml").read_text())
    named = {r["name"]: r for r in role}
    assert "Link Prowlarr Radarr Sonarr and qBittorrent using generated local API keys" in named
    assert "Check staging-only automation outcome" in named
    assert "Ensure qBittorrent local API can be used without copying passwords" in named
    assert "Create separate writable download and staging roots on system disk" in named
    assert named["Link Prowlarr Radarr Sonarr and qBittorrent using generated local API keys"]["ansible.builtin.command"]["argv"][0] == '/usr/local/sbin/dh-media-arr-wire'
    assert '("media_guest_setup", "files/dh-media-arr-wire.py")' in (ROOT / "bootstrap/semaphore-host-install.py").read_text()


def test_zfs_declared_readwrite_requires_matching_import_mode():
    script = (ROOT / "roles/media_guest_setup/templates/dh-media-zfs-mount.sh.j2").read_text()
    tasks = (ROOT / "roles/media_guest_setup/tasks/main.yml").read_text()
    assert "EXPECTED=off" in script
    assert "[[ \"$ACCESS\" == readonly ]] && EXPECTED=on" in script
    assert "MEDIA_ZFS=IMPORT_MODE_MISMATCH" in script
    assert "media_vm.storage.mode in ['readonly', 'readwrite']" in tasks
    assert "media_zfs_properties.results[0].stdout == ('on' if media_vm.storage.mode == 'readonly' else 'off')" in tasks
