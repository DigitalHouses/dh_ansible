#!/usr/bin/env python3
"""Independent, read-only recovery check of a prepared mTLS identity from Git.

Clones current main into private tmpfs, prompts for the EXISTING Fleet Vault
master password and checks that the restored key matches the CSR. No private
material is written to persistent storage or printed. Never issues certificates.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import getpass
import hashlib
import os
from pathlib import Path
import pwd
import re
import subprocess
import sys
import tempfile
import warnings

from ansible.parsing.vault import VaultLib, VaultSecret
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.x509.oid import NameOID
from datetime import datetime, timezone, timedelta
import yaml

REPO_URL = "git@github.com:DigitalHouses/dh_ansible_fleet_detail.git"
BASE = Path("/dev/shm")
HEADER = b"$ANSIBLE_VAULT;1.1;AES256\n"
EXPECTED_HOST_DOMAIN = ".digitalhouses.vip"
SITE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SHA = re.compile(r"^[a-f0-9]{64}$")
HEX32 = re.compile(r"^[a-f0-9]{32}$")


class Block(Exception):
    pass


def safe_runner(site: str):
    if not SITE.fullmatch(site):
        raise Block("INVALID_SITE_ID")
    if os.geteuid() == 0:
        raise Block("UNPRIVILEGED_OPERATOR_REQUIRED")
    legacy = (pwd.getpwuid(os.geteuid()).pw_name == "ansimble"
              and os.uname().nodename == "ansimble-" + site)
    offline = os.environ.get("DH_MTLS_OFFLINE_OPERATOR") == "1"
    if not (legacy or offline):
        raise Block("EXPLICIT_TRUSTED_OFFLINE_OPERATOR_REQUIRED")
    fs = subprocess.run(
        ["findmnt", "-T", str(BASE), "-n", "-o", "FSTYPE"],
        capture_output=True, text=True, check=False,
    )
    if fs.returncode != 0 or fs.stdout.strip() != "tmpfs":
        raise Block("RECOVERY_STAGING_NOT_TMPFS")


def subprocess_safe(argv: list[str], *, cwd=None, binary=False,
                   timeout=90) -> subprocess.CompletedProcess:
    try:
        result = subprocess.run(
            argv, cwd=cwd, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=not binary, check=False, timeout=timeout,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
    except (OSError, subprocess.TimeoutExpired):
        raise Block("RECOVERY_COMMAND_FAILED") from None
    if result.returncode != 0:
        raise Block("RECOVERY_COMMAND_FAILED")
    return result


def clone_from_github(workspace: Path) -> tuple[Path, str]:
    dest = workspace / "github"
    subprocess_safe(
        ["git", "-c", "core.hooksPath=/dev/null", "clone", "--quiet",
         "--single-branch", "--depth", "1", "--branch", "main",
         REPO_URL, str(dest)], timeout=120,
    )
    head = subprocess_safe(
        ["git", "-C", str(dest), "rev-parse", "--verify", "HEAD"]
    ).stdout.strip()
    if not re.fullmatch(r"[a-f0-9]{40}", head):
        raise Block("RECOVERY_GIT_COMMIT_INVALID")
    return dest, head


def master_from_tty() -> bytes:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            master = getpass.getpass("Existing Fleet Vault master password: ")
    except (EOFError, OSError, getpass.GetPassWarning):
        raise Block("SECURE_MASTER_INPUT_REQUIRED") from None
    if len(master) < 8 or "\r" in master or "\n" in master:
        raise Block("MASTER_FORMAT_INVALID")
    return master.encode("utf-8")


def encrypted_vault_from_clone(repository: Path, expected_sha: str) -> bytes:
    if not SHA.fullmatch(expected_sha):
        raise Block("EXPECTED_HASH_INVALID")
    vault = repository / "secrets/fleet.vault.yml"
    if vault.is_symlink() or not vault.is_file():
        raise Block("RECOVERY_TRACKED_VAULT_MISSING")
    ciphertext = vault.read_bytes()
    if not ciphertext.startswith(HEADER):
        raise Block("RECOVERY_VAULT_NOT_ENCRYPTED")
    if hashlib.sha256(ciphertext).hexdigest() != expected_sha:
        raise Block("RECOVERY_CIPHERTEXT_SHA256_MISMATCH")
    return ciphertext


def recovery_identity(ciphertext: bytes, password: bytes, site: str, expected_status: str):
    """Decrypt exactly one site generation; return only in-memory data.

    The original prepared-only entrypoint is preserved for existing tests.
    """
    if expected_status not in ("prepared", "issued_unpublished"):
        raise Block("RECOVERY_EXPECTED_STATUS_INVALID")
    vault = VaultLib([("default", VaultSecret(password))])
    try:
        payload = yaml.safe_load(vault.decrypt(ciphertext))
    except Exception:
        raise Block("RECOVERY_FLEET_DECRYPT_FAILED") from None
    if not isinstance(payload, dict):
        raise Block("RECOVERY_FLEET_PAYLOAD_INVALID")
    sites = (payload.get("fleet_secrets") or {}).get("sites")
    home = sites.get(site) if isinstance(sites, dict) else None
    if not isinstance(home, dict) or not all(
            isinstance(home.get(k), dict) for k in ("pve", "truenas", "cloudflare")):
        raise Block("RECOVERY_EXISTING_SITE_SECRETS_MISSING")
    mtls = home.get("mtls")
    if not isinstance(mtls, dict) or mtls.get("schema_version") != 1:
        raise Block("RECOVERY_MTLS_NAMESPACE_MISSING")
    generations = mtls.get("generations")
    if not isinstance(generations, list) or len(generations) != 1:
        raise Block("RECOVERY_MTLS_GENERATIONS_INVALID")
    entry = generations[0]
    host = entry.get("hostname") if isinstance(entry, dict) else None
    if (not isinstance(host, str)
            or not host.endswith(EXPECTED_HOST_DOMAIN)
            or not re.fullmatch(
                r"[a-z0-9]+(?:-[a-z0-9]+)*-ha-mtls\.digitalhouses\.vip",
                host
            )):
        raise Block("RECOVERY_MTLS_HOST_INVALID")
    if (entry.get("status") != expected_status
            or not HEX32.fullmatch(str(entry.get("generation_id")))
            or not isinstance(entry.get("validity_days"), int)
            or isinstance(entry.get("validity_days"), bool)
            or not 1 <= entry["validity_days"] <= 3650):
        raise Block("RECOVERY_MTLS_PREPARED_STATE_INVALID")
    if expected_status == "prepared" and (
        entry.get("certificate_pem") is not None
        or entry.get("cloudflare_certificate_id") is not None
    ):
        raise Block("RECOVERY_UNEXPECTED_ISSUED_CERT_STATE")
    key, csr, p12 = (
        entry.get("private_key_pem"),
        entry.get("csr_pem"),
        entry.get("p12_password"),
    )
    if (not isinstance(key, str) or
            not key.startswith(("-----BEGIN " + "PRIVATE KEY-----"))
            or not isinstance(csr, str)
            or not csr.startswith("-----BEGIN CERTIFICATE REQUEST-----")
            or not isinstance(p12, str) or len(p12) < 32):
        raise Block("RECOVERY_MTLS_SECRET_FIELDS_INVALID")
    return host, key, csr, entry["generation_id"], entry


def prepared_identity(ciphertext: bytes, password: bytes, site: str):
    host, key, csr, generation, _ = recovery_identity(
        ciphertext, password, site, "prepared"
    )
    return host, key, csr, generation


def verify_issued_identity(host: str, key_pem: str, csr_pem: str, entry: dict,
                           expected_fingerprint: str):
    """Independently verify the X.509 material restored from encrypted Git."""
    if not SHA.fullmatch(expected_fingerprint):
        raise Block("RECOVERY_EXPECTED_CERT_FINGERPRINT_INVALID")
    pem = entry.get("certificate_pem")
    cf_id = entry.get("cloudflare_certificate_id")
    stored_fingerprint = entry.get("fingerprint_sha256")
    if (not isinstance(pem, str)
            or not pem.startswith("-----BEGIN CERTIFICATE-----")
            or not isinstance(cf_id, str)
            or not re.fullmatch(r"[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}", cf_id)
            or stored_fingerprint != expected_fingerprint):
        raise Block("RECOVERY_ISSUED_CERT_METADATA_INVALID")
    try:
        cert = x509.load_pem_x509_certificate(pem.encode("ascii"))
        csr = x509.load_pem_x509_csr(csr_pem.encode("ascii"))
        private = serialization.load_pem_private_key(key_pem.encode("ascii"), password=None)
        public_bytes = lambda value: value.public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        )
        correct_subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, host)])
        if cert.subject != correct_subject or csr.subject != correct_subject:
            raise Block("RECOVERY_ISSUED_CERT_CN_INVALID")
        if not csr.is_signature_valid:
            raise Block("RECOVERY_CSR_SIGNATURE_INVALID")
        if (public_bytes(cert.public_key()) != public_bytes(csr.public_key())
                or public_bytes(cert.public_key()) != public_bytes(private.public_key())):
            raise Block("RECOVERY_ISSUED_CERT_KEY_MISMATCH")
        actual_fp = cert.fingerprint(hashes.SHA256()).hex()
        if actual_fp != expected_fingerprint:
            raise Block("RECOVERY_ISSUED_CERT_FINGERPRINT_MISMATCH")
        now = datetime.now(timezone.utc)
        if not cert.not_valid_before_utc <= now <= cert.not_valid_after_utc:
            raise Block("RECOVERY_ISSUED_CERT_EXPIRED_OR_NOT_YET_VALID")
        days = entry["validity_days"]
        duration = cert.not_valid_after_utc - cert.not_valid_before_utc
        if not timedelta(days=days-1) <= duration <= timedelta(days=days+1):
            raise Block("RECOVERY_ISSUED_CERT_VALIDITY_MISMATCH")
    except (TypeError, ValueError, AttributeError, KeyError, UnicodeError):
        raise Block("RECOVERY_ISSUED_CERT_MALFORMED") from None
    return cf_id


@contextmanager
def recovered_material(workspace: Path, private_key: str, csr: str):
    directory = workspace / "key-validation"
    directory.mkdir(mode=0o700, exist_ok=False)
    paths = []
    try:
        for name, value in (("private.key", private_key), ("request.csr", csr)):
            path = directory / name
            with path.open("xb") as file:
                paths.append(path)  # Track immediately for cleanup even on short/failed writes
                os.fchmod(file.fileno(), 0o600)
                file.write(value.encode("ascii"))
        yield paths[0], paths[1]
    finally:
        for path in paths:
            path.unlink(missing_ok=True)
        directory.rmdir()


def verify_crypto(key: Path, csr: Path, hostname: str):
    subject = subprocess_safe([
        "openssl", "req", "-in", str(csr), "-noout",
        "-subject", "-nameopt", "RFC2253",
    ]).stdout.strip()
    if subject != "subject=CN=" + hostname:
        raise Block("RECOVERY_CSR_SUBJECT_MISMATCH")
    subprocess_safe(["openssl", "req", "-in", str(csr), "-noout", "-verify"])
    key_pub = subprocess_safe(
        ["openssl", "pkey", "-in", str(key), "-pubout"]
    ).stdout
    csr_pub = subprocess_safe(
        ["openssl", "req", "-in", str(csr), "-pubkey", "-noout"]
    ).stdout
    if key_pub != csr_pub:
        raise Block("RECOVERY_KEY_CSR_MISMATCH")
    public_info = subprocess_safe(
        ["openssl", "pkey", "-in", str(key), "-noout", "-text_pub"]
    ).stdout
    if not public_info.startswith("Public-Key: (2048 bit)"):
        raise Block("RECOVERY_KEY_NOT_RSA_2048")
    return True


def run(site: str, expected_sha: str, expected_status: str = "prepared",
        expected_fingerprint: str | None = None):
    safe_runner(site)
    if expected_status not in ("prepared", "issued_unpublished"):
        raise Block("RECOVERY_EXPECTED_STATUS_INVALID")
    if expected_status == "issued_unpublished" and expected_fingerprint is None:
        raise Block("RECOVERY_ISSUED_FINGERPRINT_PIN_REQUIRED")
    print("MODE=FRESH_GITHUB_CLONE_MTLS_RECOVERY_READ_ONLY")
    print("SITE=" + site)
    with tempfile.TemporaryDirectory(prefix="dh-mtls-recovery-", dir=BASE) as dirname:
        workspace = Path(dirname)
        workspace.chmod(0o700)
        repository, head = clone_from_github(workspace)
        print("FRESH_GIT_CLONE=PASS")
        print("GIT_MAIN_HEAD=" + head[:12])
        ciphertext = encrypted_vault_from_clone(repository, expected_sha)
        print("GIT_VAULT_SHA256=PASS")
        password = master_from_tty()
        hostname, key, csr, generation, entry = recovery_identity(
            ciphertext, password, site, expected_status
        )
        print("ENCRYPTED_FLEET_VAULT_RECOVERY=PASS")
        with recovered_material(workspace, key, csr) as (key_file, csr_file):
            verify_crypto(key_file, csr_file, hostname)
        print("RECOVERED_KEY_AND_CSR=PASS")
        if expected_status == "issued_unpublished":
            cf_id = verify_issued_identity(
                hostname, key, csr, entry, expected_fingerprint
            )
            print("ISSUED_CERTIFICATE_X509_AND_KEY=PASS")
            print("ISSUED_CERTIFICATE_SHA256=PASS")
            print("CLOUDFLARE_CERTIFICATE_ID=" + cf_id)
        print("CSR_CN=CN=" + hostname)
        print("GENERATION_ID=" + generation)
        print("RECOVERED_CERTIFICATE_STATE=" + expected_status)
    print("TEMPORARY_RECOVERY_MATERIAL_DELETED=PASS")
    print("CERTIFICATE_ISSUANCE_AUTHORIZED=NO")
    print("CLOUDFLARE_MUTATIONS=NONE")
    print("TRUE_NAS_MUTATIONS=NONE")


if __name__ == "__main__":
    try:
        parser = argparse.ArgumentParser()
        parser.add_argument("--site", required=True)
        parser.add_argument("--expected-sha256", required=True)
        parser.add_argument("--expected-status", choices=["prepared", "issued_unpublished"],
                            default="prepared")
        parser.add_argument("--expected-cert-fingerprint-sha256")
        parser.add_argument("--verify-read-only", action="store_true", required=True)
        args = parser.parse_args()
        run(args.site, args.expected_sha256, args.expected_status,
            args.expected_cert_fingerprint_sha256)
    except Block as e:
        print("BLOCK=" + str(e), file=sys.stderr)
        sys.exit(2)
    except Exception as e:
        print("BLOCK=UNEXPECTED_" + type(e).__name__, file=sys.stderr)
        sys.exit(2)
    finally:
        print("=== DONE ===")
        print("=== TERMINAL STAYS OPEN ===")
