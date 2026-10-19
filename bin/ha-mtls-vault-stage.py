#!/usr/bin/env python3
"""Stage a recoverable mTLS key in the EXISTING encrypted Fleet Vault.

No Cloudflare API calls, certificate issuance, Git writes or pushes.
Only an encrypted, temporary candidate Vault and nonsecret manifest are staged.
A separate CI-reviewed GitHub PR must merge before any certificate can be issued.

Run interactively as the unprivileged site-runner user.
"""
from __future__ import annotations

from datetime import datetime, timezone
import getpass
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import pwd
import re
import secrets
import stat
import subprocess
import sys
import warnings

from ansible.parsing.vault import VaultLib, VaultSecret
import yaml

ROOT = Path(__file__).resolve().parents[1]
FLEET_ROOT = Path(os.environ.get("DH_FLEET_DETAIL_DIR", "/var/lib/ansimble/fleet-detail"))
VAULT_REL = "secrets/fleet.vault.yml"
STAGE_BASE = Path("/dev/shm")
SUPPORTED_PHASE = "prepared"
SITE_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
GIT_HEAD_RE = re.compile(r"^[0-9a-f]{40}$")
ENCRYPTED_HEADER = b"$ANSIBLE_VAULT;1.1;AES256\n"


class Block(Exception):
    pass


def require_runner(site: str):
    if not SITE_RE.fullmatch(site):
        raise Block("SITE_ID_INVALID")
    if (pwd.getpwuid(os.geteuid()).pw_name != "ansimble"
            or os.geteuid() == 0
            or os.uname().nodename != "ansimble-" + site):
        raise Block("REQUIRES_UNPRIVILEGED_SITE_RUNNER")


def plan_module():
    path = ROOT / "bin" / "ha-mtls-certificate-plan.py"
    spec = importlib.util.spec_from_file_location("ha_mtls_certificate_plan", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def git(*args: str):
    result = subprocess.run(
        ["git", "-C", str(FLEET_ROOT), *args],
        stdin=subprocess.DEVNULL, capture_output=True, text=True,
        timeout=90, check=False,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
    )
    if result.returncode != 0:
        raise Block("GIT_PREFLIGHT_FAILED")
    return result.stdout.strip()


def repo_guard():
    if git("branch", "--show-current") != "main":
        raise Block("NOT_ON_MAIN")
    if git("status", "--porcelain", "--untracked-files=all"):
        raise Block("GIT_WORKTREE_NOT_CLEAN")
    git("fetch", "--quiet", "origin", "main")
    head = git("rev-parse", "HEAD")
    if not GIT_HEAD_RE.fullmatch(head) or git("rev-parse", "origin/main") != head:
        raise Block("MAIN_NOT_SYNCHRONIZED")
    return head


def vault_guard(path: Path = FLEET_ROOT / VAULT_REL) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise Block("ENCRYPTED_FLEET_VAULT_NOT_REGULAR_FILE")
    info = path.stat()
    if (info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o600):
        raise Block("FLEET_VAULT_PERMISSIONS_INVALID")
    ciphertext = path.read_bytes()
    if not ciphertext.startswith(ENCRYPTED_HEADER):
        raise Block("FLEET_VAULT_IS_NOT_ENCRYPTED")
    return ciphertext


def encrypted_stage_directory(site: str):
    base = STAGE_BASE / ("dh-mtls-" + site + "-encrypted-stage")
    if not STAGE_BASE.is_dir():
        raise Block("STAGING_TMPFS_ABSENT")
    fs = subprocess.run(
        ["findmnt", "-T", str(STAGE_BASE), "-n", "-o", "FSTYPE"],
        capture_output=True, text=True, check=False,
    )
    if fs.returncode != 0 or fs.stdout.strip() != "tmpfs":
        raise Block("STAGING_FILESYSTEM_NOT_TMPFS")
    if base.is_symlink():
        raise Block("STAGING_SYMLINK_FORBIDDEN")
    if base.exists():
        if (not base.is_dir()
                or base.stat().st_uid != os.geteuid()
                or stat.S_IMODE(base.stat().st_mode) != 0o700):
            raise Block("STAGING_DIRECTORY_PERMISSIONS_INVALID")
    return base


def master_input() -> bytes:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            master = getpass.getpass("Existing Fleet Vault master password: ")
    except (EOFError, OSError, getpass.GetPassWarning):
        raise Block("SECURE_INTERACTIVE_MASTER_INPUT_REQUIRED") from None
    if len(master) < 8 or "\n" in master or "\r" in master:
        raise Block("MASTER_INPUT_INVALID")
    return master.encode("utf-8")


def decrypt_fleet(ciphertext: bytes, password: bytes):
    crypt = VaultLib([("default", VaultSecret(password))])
    try:
        original = yaml.safe_load(crypt.decrypt(ciphertext))
    except Exception:
        raise Block("FLEET_VAULT_DECRYPT_FAILED") from None
    if (not isinstance(original, dict)
            or not isinstance(original.get("fleet_secrets"), dict)
            or not isinstance(original["fleet_secrets"].get("sites"), dict)):
        raise Block("FLEET_VAULT_STRUCTURE_CHANGED")
    return crypt, original


def append_prepared_generation(payload: dict, site: str, state: dict,
                               key_pem: str, csr_pem: str, password: str,
                               generation: str, created: str):
    """Pure in-memory operation. Existing site credentials remain untouched."""
    sites = payload["fleet_secrets"]["sites"]
    site_secrets = sites.get(site)
    if not isinstance(site_secrets, dict):
        raise Block("SITE_VAULT_NAMESPACE_NOT_PRESENT")
    if "mtls" in site_secrets:
        raise Block("MTLS_EXISTS_REQUIRES_SEPARATE_ROTATION_WORKFLOW")
    if not (key_pem.startswith(("-----BEGIN " + "PRIVATE KEY-----"))
            and csr_pem.startswith("-----BEGIN CERTIFICATE REQUEST-----")):
        raise Block("OPENSSL_PEM_FORMAT_UNEXPECTED")
    if not isinstance(password, str) or len(password) < 32:
        raise Block("P12_PASSWORD_TOO_SHORT")
    if not re.fullmatch(r"[0-9a-f]{32}", generation):
        raise Block("GENERATION_ID_INVALID")
    site_secrets["mtls"] = {
        "schema_version": 1,
        "generations": [{
            "generation_id": generation,
            "status": SUPPORTED_PHASE,
            "hostname": state["hostname"],
            "created_at_utc": created,
            "validity_days": state.get("cert_validity_days", 365),
            "private_key_pem": key_pem,
            "csr_pem": csr_pem,
            "p12_password": password,
            "certificate_pem": None,
            "cloudflare_certificate_id": None,
        }],
    }
    return payload


def encrypt_candidate(ciphertext: bytes, master: bytes, site: str, state: dict,
                      key_pem: str, csr_pem: str, p12_password: str,
                      generation: str, created: str):
    crypt, payload = decrypt_fleet(ciphertext, master)
    append_prepared_generation(payload, site, state, key_pem, csr_pem,
                               p12_password, generation, created)
    serialized = yaml.safe_dump(payload, allow_unicode=True, sort_keys=False).encode()
    proposed = crypt.encrypt(serialized)
    if (not proposed.startswith(ENCRYPTED_HEADER)
            or crypt.decrypt(proposed) != serialized
            or any(part in proposed for part in
                   (key_pem.encode(), csr_pem.encode(), p12_password.encode()))):
        raise Block("ENCRYPTED_CANDIDATE_ROUNDTRIP_FAILED")
    verified = yaml.safe_load(crypt.decrypt(proposed))
    generations = verified["fleet_secrets"]["sites"][site]["mtls"]["generations"]
    if len(generations) != 1 or generations[0]["generation_id"] != generation:
        raise Block("ENCRYPTED_CANDIDATE_VERIFY_FAILED")
    return proposed


def stage_files(directory: Path, encrypted: bytes, manifest: dict):
    """Create only encrypted bytes + public metadata, without mutating Git."""
    directory.mkdir(mode=0o700, exist_ok=False)
    try:
        vault = directory / "fleet.vault.yml"
        info = directory / "manifest.json"
        for path, content in (
            (vault, encrypted),
            (info, (json.dumps(manifest, sort_keys=True) + "\n").encode()),
        ):
            with path.open("xb") as output:
                os.fchmod(output.fileno(), 0o600)
                output.write(content)
                output.flush()
                os.fsync(output.fileno())
        if (vault.read_bytes() != encrypted
                or json.loads(info.read_text()) != manifest):
            raise Block("STAGING_READBACK_FAILED")
    except Exception:
        for name in ("fleet.vault.yml", "manifest.json"):
            (directory / name).unlink(missing_ok=True)
        directory.rmdir()
        raise


def run(site: str):
    require_runner(site)
    plan = plan_module()
    state = plan.intent(site)
    head = repo_guard()
    existing = vault_guard()
    stage = encrypted_stage_directory(site)
    if stage.exists():
        raise Block("ENCRYPTED_STAGE_ALREADY_EXISTS_REVIEW_OR_CLEANUP")
    print("MODE=ENCRYPTED_VAULT_STAGE_NO_CF_WRITES")
    print("SITE=" + site)
    print("REPO_HEAD=" + head[:12])
    print("SOURCE_VAULT_SHA256=" + hashlib.sha256(existing).hexdigest())
    # No key generation until the correct existing master password is checked.
    master = master_input()
    decrypt_fleet(existing, master)
    generation = secrets.token_hex(16)
    p12_password = secrets.token_urlsafe(40)
    created = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        with plan.disposable_csr(state["hostname"]) as (key, csr):
            candidate = encrypt_candidate(
                existing, master, site, state,
                key.read_text(encoding="ascii"), csr.read_text(encoding="ascii"),
                p12_password, generation, created,
            )
    except plan.Block as e:
        raise Block(str(e)) from None

    manifest = {
        "site": site,
        "hostname": state["hostname"],
        "generation_id": generation,
        "expected_repo_head": head,
        "source_vault_sha256": hashlib.sha256(existing).hexdigest(),
        "candidate_vault_sha256": hashlib.sha256(candidate).hexdigest(),
        "state": "ENCRYPTED_STAGE_NOT_COMMITTED",
    }
    stage_files(stage, candidate, manifest)
    print("CSR_CN=CN=" + state["hostname"])
    print("KEY_AND_CSR=RECOVERABLE_ONLY_AFTER_GITHUB_MERGE")
    print("NEW_VAULT_CIPHERTEXT_VERIFIED=PASS")
    print("STAGED_ENCRYPTED_FILE=" + str(stage / "fleet.vault.yml"))
    print("STAGED_METADATA=" + str(stage / "manifest.json"))
    print("STAGED_CANDIDATE_SHA256=" + manifest["candidate_vault_sha256"])
    print("PRODUCTION_GIT_WORKTREE_UNCHANGED=YES")
    print("GITHUB_PR_AND_FRESH_CLONE_RECOVERY_REQUIRED=YES")
    print("CLOUDFLARE_MUTATIONS=NONE")
    print("CERTIFICATE_ISSUANCE_AUTHORIZED=NO")


if __name__ == "__main__":
    try:
        if len(sys.argv) != 4 or sys.argv[1] != "--site" or sys.argv[3] != "--stage-encrypted-only":
            raise Block("USAGE_--site_SITE_--stage-encrypted-only")
        run(sys.argv[2])
    except Block as exc:
        print("BLOCK=" + str(exc), file=sys.stderr)
        sys.exit(2)
    except Exception as exc:
        print("BLOCK=UNEXPECTED_" + type(exc).__name__, file=sys.stderr)
        sys.exit(2)
    finally:
        print("=== DONE ===")
        print("=== TERMINAL STAYS OPEN ===")
