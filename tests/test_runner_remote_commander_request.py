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
    assert "remote_request_hostname.stdout == 'ansible-' ~ site.owner_slug" in src
    assert "dh-remote-commander-install.path" in src
    assert 'content: "INSTALL\\n"' in src
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
