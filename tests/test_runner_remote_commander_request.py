"""Offline contract for the tightly scoped on-demand local installer."""
from pathlib import Path
import yaml
ROOT=Path(__file__).resolve().parents[1]

def test_request_is_local_and_unprivileged():
    src=(ROOT / "playbooks/request-runner-remote-commander.yml").read_text()
    p=yaml.safe_load(src)[0]
    assert p["hosts"] == "site_runner"
    assert p["become"] is False
    assert p["gather_facts"] is False
    assert "remote_request_hostname.stdout == 'ansible-' ~ site.id" in src
    assert "dh-remote-commander-install.path" in src
    assert "'PAIR' if remote_request_existing.stdout == 'active' else 'INSTALL'" in src
    assert "remote_request_id.stdout" in src
    assert "remote_pairing_record.stdout.split('|')[3]" in src
    assert "remote_pairing_record.stdout.split('|')[4]" in src
    assert "sudo" not in src

def test_root_watcher_is_single_purpose():
    src=(ROOT / "bootstrap/remote-commander-install-request.sh").read_text()
    assert "ANSIBLE_ROLES_PATH=" in src
    assert "apply-runner-remote-commander.yml" in src
    assert "dh-remote-commander.request" in src
    assert "pct " not in src and "qm " not in src
    unit=(ROOT / "bootstrap/dh-remote-commander-install.service").read_text()
    path=(ROOT / "bootstrap/dh-remote-commander-install.path").read_text()
    assert "User=root" in unit
    assert "ExecStart=/usr/local/sbin/dh-remote-commander-install" in unit
    assert "PathExists=/var/lib/semaphore/dh-remote-commander.request" in path

def test_request_shows_pairing_url_before_waiting():
    src=(ROOT / "playbooks/request-runner-remote-commander.yml").read_text()
    tasks=yaml.safe_load(src)[0]["tasks"]
    names=[task["name"] for task in tasks]
    assert "Explain installation progress and device pairing" in names
    assert names.index("Explain installation progress and device pairing") < names.index("Await Remote Commander installation")
    assert "https://mcp.desktopcommander.app/device/verify" in src
    assert "Show actual one-time pairing code and URL inside Semaphore" in names
    assert names.index("Show actual one-time pairing code and URL inside Semaphore") > names.index("Await sanitized OAuth pairing status")
    assert "mode: \"0600\"" in src
    assert "remote_pairing_record.stdout.startswith('PAIR|' ~ remote_request_id.stdout ~ '|')" in src

def test_pairing_export_remains_root_only_with_narrow_semaphore_view():
    src=(ROOT / "bootstrap/remote-commander-install-request.sh").read_text()
    assert "ACTION=" in src and "NONCE=" in src
    assert "INSTALL|PAIR" in src
    assert "journalctl" in src
    assert '"_SYSTEMD_INVOCATION_ID=$invocation"' in src
    assert "chown root:semaphore" in src
    assert "chmod 0640" in src
    assert 'publish_pairing "CODE|$code|$url"' in src
    assert "PAIR_FILE=/run/dh-remote-commander-pairing.status" in src
    assert "if [[ \"$ACTION\" == PAIR ]]" in src
    assert "PAIR_URL=https://mcp.desktopcommander.app/device/verify" in src
