"""Static security and syntax contracts for PVE/runner site-data providers."""
from pathlib import Path
import ast

ROOT = Path(__file__).resolve().parents[1]
PVE = ROOT / "bootstrap/dh-site-data-pve-refresh.py"
RUNNER = ROOT / "bootstrap/dh-site-data-runner.py"


def test_helper_python_syntax():
    for file in (PVE, RUNNER):
        ast.parse(file.read_text(), filename=str(file))


def test_fleet_provider_is_fixed_scope_and_pinned():
    s = PVE.read_text()
    for term in ("SITE_RE", "COMPONENTS", "FLEET_BLOB_SHA_MISMATCH",
                 "FLEET_REF_INVALID", "SITE_DATA_SOURCE_SHA",
                 "EXISTING_SITE_FILE_SYMLINK_BLOCKED",
                 "LOCAL_SITE_DATA_DRIFT_BLOCKED",
                 "sha256", "fcntl.flock"):
        assert term in s, term
    assert "secrets/fleet.vault.yml" not in s
    assert "subprocess.run(cmd, shell=True" not in s
    assert 'mode="0640"' in s


def test_runner_only_calls_verified_fixed_pve_helper():
    s = RUNNER.read_text()
    assert "/usr/local/sbin/dh-site-data-pve-refresh" in s
    assert "StrictHostKeyChecking=yes" in s
    assert "SITE_DATA:(" in s
    assert "PVE_SITE_DATA_PROVIDER_FAILED" in s
    assert "subprocess.run(cmd, capture_output=True" in s
    assert "shell=True" not in s
    assert "gh auth login" not in s
    assert "git clone" not in s


def test_worker_units_preserve_privilege_split():
    service = (ROOT / "bootstrap/dh-site-data.service").read_text()
    watcher = (ROOT / "bootstrap/dh-site-data.path").read_text()
    assert "User=root" in service
    assert "NoNewPrivileges=true" in service
    assert "ExecStart=/usr/local/sbin/dh-site-data-runner" in service
    assert "PathExists=/var/lib/semaphore/dh-site-data.request" in watcher
