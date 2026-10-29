"""Ensure secure usable PVE serial console with Intel GPU passthrough."""
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[1]
GUEST = ROOT / "roles/media_guest_setup/files/dh-media-serial-console-login.py"
PVE = ROOT / "bootstrap/dh-pve-media-console-password-show.py"
TASKS = ROOT / "roles/media_guest_setup/tasks/serial_console.yml"


def test_serial_console_has_independent_protected_credential():
    code = GUEST.read_text()
    assert "CREDENTIAL = Path(\"/root/.dh-media-console-login\")" in code
    assert "ALPHABET = string.ascii_letters + string.digits" in code
    assert 'range(20)' in code
    assert "os.O_NOFOLLOW" in code
    assert "0o600" in code
    assert '["/usr/sbin/chpasswd"]' in code
    assert 'passwordauthentication no' in code
    assert 'kbdinteractiveauthentication no' in code
    assert "autologin" not in code.lower()
    assert "MEDIA_SERIAL_CONSOLE=CREATED" in code
    assert "MEDIA_SERIAL_CONSOLE=READY" in code


def test_console_helper_requires_pve_root_and_validates_vm_identity():
    code = PVE.read_text()
    assert 'VMID = "2238"' in code
    assert 'VMNAME = "mediaserver-serzhan"' in code
    assert 'GUEST_HELPER = "/usr/local/sbin/dh-media-serial-console-login"' in code
    assert 'os.geteuid() != 0' in code
    assert 'password=[A-Za-z0-9]{20}' in code
    assert 'username=debian' in code
    assert '"--check"' in code


def test_guest_role_provisions_idempotently_without_leaking():
    tasks = yaml.safe_load(TASKS.read_text())
    by = {task["name"]:task for task in tasks}
    assert "Ensure Debian serial console login is available on PVE web console" in by
    assert "Provision serial console access without exposing its password" in by
    assert by["Provision serial console access without exposing its password"]["no_log"] is True
    assert by["Provision serial console access without exposing its password"]["changed_when"]
    src = (ROOT / "roles/media_guest_setup/tasks/main.yml").read_text()
    assert "import_tasks: serial_console.yml" in src
    pin = (ROOT / "bootstrap/semaphore-host-install.py").read_text()
    assert "bootstrap/dh-pve-media-console-password-show.py" in pin
    assert "files/dh-media-serial-console-login.py" in pin
    assert "tasks/serial_console.yml" in pin
    assert "/usr/local/sbin/dh-media-console-password-show" in pin
    assert "dh-media-password-show" in pin
