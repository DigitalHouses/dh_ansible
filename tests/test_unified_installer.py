"""Offline contract: public single-repository installer without private credentials."""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
INSTALL = ROOT / "bootstrap/install.sh"


def test_installer_is_present_and_pins_independent_immutable_inputs():
    s = INSTALL.read_text()
    assert re.search(r'^CODE_RELEASE="[a-f0-9]{40}"$', s, re.M)
    assert re.search(r'^CODE_INSTALLER_BLOB="[a-f0-9]{40}"$', s, re.M)
    assert re.search(r'^FLEET_RELEASE="[a-f0-9]{40}"$', s, re.M)
    assert re.search(r'^GH_SHA256="[a-f0-9]{64}"$', s, re.M)
    assert "repos/DigitalHouses/dh_ansible/contents/bootstrap/semaphore-host-install.py?ref=$CODE_RELEASE" in s
    assert "CODE_INSTALLER_BLOB" in s and "identity != expected_sha" in s
    assert "--expected-fleet-commit \"$FLEET_RELEASE\"" in s
    assert "--use-github-session --apply" in s
    assert "https://github.com/login/device" in s
    assert "dh_ansimble_fleet_detail" not in s
    assert "ansimble-installer" not in s
    assert "SITE_CONFIRMATION=YES $SITE" in s
    assert "read -r -p \"Подтвердить объект" in s
    assert 'case "$CONFIRM_SITE"' in s
    assert '  --site "$SITE"' in s
    assert 'echo "OBJECT_SLUG=$SITE"' in s


def test_installer_blocks_occupied_ct_and_has_no_destructive_actions():
    s = INSTALL.read_text()
    assert "EUID != 0" in s
    assert "/etc/pve/user.cfg" in s
    assert "--resume-semaphore" in s and "--resume-ansible" in s
    assert "rm -rf \"$WORK\"" in s
    assert "pct destroy" not in s
    assert "qm destroy" not in s
    assert "pveam remove" not in s
    assert "GH_CONFIG_DIR=\"$WORK/gh-config\"" in s
