"""Contract for root-only PVE recovery of current Media Server owner password."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GUEST = ROOT / "roles/media_guest_setup/files/dh-media-password-current.py"
HOST = ROOT / "bootstrap/dh-pve-media-password-show.py"


def test_guest_helper_is_root_only_and_never_uses_an_ansible_vault():
    source = GUEST.read_text()
    assert "os.geteuid() != 0" in source
    assert "media-password-rotation.json" in source
    assert '"credentials"' in source
    assert "lstat()" in source
    assert "stat.S_ISREG" in source
    assert "print(\"password=\" + password)" in source
    assert "import ansible" not in source.lower()


def test_pve_password_recovery_pins_guest_identity_and_qga():
    source = HOST.read_text()
    assert 'VMID = "2238"' in source
    assert 'VMNAME = "mediaserver-serzhan"' in source
    assert 'GUEST_HELPER = "/usr/local/sbin/dh-media-password-current"' in source
    assert '"guest", "exec", VMID' in source
    assert "os.geteuid() != 0" in source
    assert "getpass" not in source.lower()
    assert 'parser.add_argument("--check"' in source
    assert "import ansible" not in source.lower()


def test_guest_helper_in_protected_provisioning_manifest():
    guest = (ROOT / "roles/media_guest_setup/tasks/password_rotation.yml").read_text()
    bootstrap = (ROOT / "bootstrap/semaphore-host-install.py").read_text()
    assert "dh-media-password-current.py" in guest
    assert '"roles/media_guest_setup/files/dh-media-password-current.py"' in bootstrap
    assert '("media_guest_setup", "files/dh-media-password-current.py")' in bootstrap
