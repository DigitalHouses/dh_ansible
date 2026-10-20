"""Offline safety contracts for read-only clean runner rebuild preflight."""
import importlib.util
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
SCRIPT=ROOT/"bootstrap/runner-rebuild-preflight.py"

def load():
    spec=importlib.util.spec_from_file_location("runner_rebuild_preflight",SCRIPT)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def test_parser_distinguishes_guest_mounts_from_known_config():
    m=load()
    data=m.parse_pve_config(
      "hostname: ansible-home\nunprivileged: 1\nnet0: name=eth0,ip=198.51.100.231/24,bridge=vmbr0\n"
    )
    assert data["hostname"]=="ansible-home"
    assert data["unprivileged"]=="1"
    assert data["net0"].startswith("name=eth0")

def test_rebuild_preflight_is_read_only_and_secret_safe():
    text=SCRIPT.read_text()
    assert "pct\", \"config\"" in text
    assert "pvesm\", \"status\"" in text
    assert "DESTRUCTIVE_ACTION=NONE" in text
    assert "BACKUP_REQUIRED=YES" in text
    for bad in ('"pct", "destroy"', '"pct", "create"',
                '"pveum", "acl", "modify"', '"pveum", "user", "token", "add"'):
        assert bad not in text
    assert 'print(data)' not in text
    assert "SENSITIVE_CONTENT_PRINTED=NO" in text

def test_root_only_staging_check_present():
    src=SCRIPT.read_text()
    assert "stat.S_IMODE(stage.stat().st_mode) == 0o600" in src
    assert 'set(data.get("pve_tokens", {})) == {"audit", "write"}' in src
