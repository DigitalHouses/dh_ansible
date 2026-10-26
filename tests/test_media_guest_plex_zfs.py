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


def test_readonly_zfs_excludes_reformatting_and_backup_mounts():
    script = (ROOT / "roles/media_guest_setup/templates/dh-media-zfs-mount.sh.j2").read_text()
    for term in ("readonly=on", "pool_guid", "partition_partuuid",
                 "media_dataset", "MEDIA_ZFS=READY_READONLY"):
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
