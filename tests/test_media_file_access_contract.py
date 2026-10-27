"""Safety contract for private Fleet-backed Samba credentials."""
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[1]
ROLE = ROOT / "roles/media_guest_setup"


def test_early_preflight_before_worker_deploy():
    tasks = yaml.safe_load((ROOT / "playbooks/plan-pve-media-vm.yml").read_text())[0]["pre_tasks"]
    names = [t["name"] for t in tasks]
    assert names.index("Fail closed unless private Fleet declares SMB access identities") < names.index("Validate canonical storage inventory")
    assert "MEDIA_FILE_ACCESS_FLEET_REQUIRED" in str(tasks)


def test_smb_accounts_provisioned_before_smb_conf():
    tasks = yaml.safe_load((ROLE / "tasks/main.yml").read_text())
    names = [t["name"] for t in tasks]
    assert names.index("Require Fleet identities and provision authenticated SMB access") < names.index("Advertise existing media library over SMB")
    inc = tasks[names.index("Require Fleet identities and provision authenticated SMB access")]
    assert inc["ansible.builtin.import_tasks"] == "file_access.yml"
    assert "Confirm readonly guest SMB share is reachable" not in names
    assert names.index("Reject anonymous access to authenticated Media share") < names.index("Require anonymous Media access to be denied")


def test_account_validation_and_secrets_are_private():
    tasks = yaml.safe_load((ROLE / "tasks/file_access.yml").read_text())
    named = {t["name"]: t for t in tasks}
    assert named["Require explicit Samba accounts and writable media pool"]["no_log"] is True
    assert named["Validate media account names roles and password strength"]["no_log"] is True
    create = named["Create declared non-login SMB identities"]
    assert create["no_log"] is True
    assert create["ansible.builtin.user"]["shell"] == "/usr/sbin/nologin"
    assert create["ansible.builtin.user"]["password_lock"] is True
    smb = named["Reconcile Samba passwords exclusively from private Fleet"]
    assert smb["no_log"] is True
    assert smb["ansible.builtin.command"]["argv"][:3] == ["smbpasswd", "-s", "-a"]
    assert "item.password" in smb["ansible.builtin.command"]["stdin"]
    assert named["Permit authenticated writers at the media dataset root"]["ansible.builtin.file"]["mode"] == "2775"
    assert "recurse" not in named["Permit authenticated writers at the media dataset root"]["ansible.builtin.file"]


def test_media_smb_is_authenticated_and_reader_writer_split():
    text = (ROLE / "templates/media-smb.conf.j2").read_text()
    for expected in ("guest ok = no", "map to guest = Never",
                     "valid users = @dh_media_readers @dh_media_writers",
                     "write list = @dh_media_writers",
                     "force group = media_group"):
        assert expected in text
    for rejected in ("guest only = yes", "guest ok = yes", "force user = nobody"):
        assert rejected not in text
