"""Synthetic regression tests for fail-closed public boundary policies."""
from pathlib import Path
import shutil
import subprocess
import sys
import runpy

ROOT = Path(__file__).resolve().parents[1]
POLICY = runpy.run_path(str(ROOT / "bin/public-boundary-check.py"))


def matches_content(data: bytes) -> bool:
    return any(pattern.search(data) for pattern in POLICY["BLOCKED_CONTENT"])


def matches_path(path: str) -> bool:
    return any(pattern.search(path) for pattern in POLICY["BLOCKED_PATHS"])


def test_private_key_header_is_detected_even_inside_a_file():
    header = b"-----BEGIN " + b"OPENSSH PRIVATE KEY-----"
    assert matches_content(b"# not a key\n" + header + b"\n")


def test_rfc1918_site_addresses_are_rejected():
    for address in (
        b"192." + b"168.8.12",
        b"10." + b"25.1.2",
        b"172." + b"16.3.4",
        b"172." + b"31.255.254",
    ):
        assert matches_content(b"host=" + address)


def test_documentation_ips_and_generic_site_paths_are_allowed():
    assert not matches_content(b"host=198.51.100.20")
    assert not matches_content(b"host=203.0.113.12")
    assert not matches_path("inventories/example/hosts.yml")
    assert matches_path("inventories/customer_x/hosts.yml")
    assert matches_path("secrets/fleet.vault.yml")


def test_mtls_planner_and_issuer_do_not_embed_site_identity():
    planner = (ROOT / "bin/ha-mtls-certificate-plan.py").read_text()
    issuer = (ROOT / "bin/ha-mtls-certificate-issue.py").read_text()
    assert "REQUIRES_CT1900_ROOT" not in issuer
    assert 'def token_for_site(site:' in planner
    assert 'def root_and_repository(expected_sha, site):' in issuer
    assert 'legacy = state["legacy_guard"]' in planner
    assert 'str(FLEET_ROOT), *args' in issuer
    assert '"ansimble-" + site' in issuer

def test_history_scan_rejects_deleted_private_path(tmp_path):
    repository = tmp_path / "repo"
    (repository / "bin").mkdir(parents=True)
    (repository / "docs" / "baselines").mkdir(parents=True)
    shutil.copyfile(ROOT / "bin/public-boundary-check.py",
                    repository / "bin/public-boundary-check.py")
    private_doc = repository / "docs/baselines/deleted-site.md"
    private_doc.write_text("synthetic operational note", encoding="utf-8")

    def git(*args):
        subprocess.run(["git", "-C", str(repository), *args],
                       check=True, capture_output=True)
    git("init", "-q")
    git("config", "user.name", "Synthetic Test")
    git("config", "user.email", "test@example.invalid")
    git("add", ".")
    git("commit", "-qm", "fixture add")
    git("rm", "-q", "docs/baselines/deleted-site.md")
    git("commit", "-qm", "fixture remove")

    checker = repository / "bin/public-boundary-check.py"
    command = [sys.executable, str(checker)]
    current = subprocess.run(command, capture_output=True, text=True)
    assert current.returncode == 0
    historical = subprocess.run(command + ["--history"],
                                capture_output=True, text=True)
    assert historical.returncode == 1
    assert "PUBLIC_HISTORY=FAIL" in historical.stdout
    assert "PUBLIC_HISTORY_SENSITIVE_PATHS=1" in historical.stdout
    assert "synthetic operational note" not in historical.stdout


def test_history_scan_accepts_clean_root_and_rejects_deleted_token(tmp_path):
    repository = tmp_path / "repo"
    (repository / "bin").mkdir(parents=True)
    shutil.copyfile(ROOT / "bin/public-boundary-check.py",
                    repository / "bin/public-boundary-check.py")

    def git(*args):
        subprocess.run(["git", "-C", str(repository), *args],
                       check=True, capture_output=True)
    git("init", "-q")
    git("config", "user.name", "Synthetic Test")
    git("config", "user.email", "test@example.invalid")
    git("add", ".")
    git("commit", "-qm", "clean source root")
    cmd = [sys.executable, str(repository / "bin/public-boundary-check.py"),
           "--history"]
    assert subprocess.run(cmd, capture_output=True).returncode == 0

    marker = repository / "runtime-fixture.txt"
    marker.write_text("gh" + "p_" + ("T" * 32), encoding="ascii")
    git("add", ".")
    git("commit", "-qm", "fixture secret inserted")
    git("rm", "-q", marker.name)
    git("commit", "-qm", "fixture secret deleted")
    historic = subprocess.run(cmd, capture_output=True, text=True)
    assert historic.returncode == 1
    assert "PUBLIC_HISTORY_SENSITIVE_BLOBS=1" in historic.stdout
    assert "T" * 32 not in historic.stdout
