#!/usr/bin/env python3
"""Guarded Cloudflare Managed-CA issuance from the recoverable Fleet Vault CSR.

Modes:
  --preflight: Cloudflare GET only. Does not issue or stage anything.
  --issue-once: explicit one-time POST scoped to an approved generation,
                ciphertext hash, hostname, 365-day validity and short expiry.
                The global fleet write gate remains disabled.

The already-Git-backed RSA private key never leaves memory. The returned
certificate is verified before a new encrypted Fleet Vault candidate is
written to private tmpfs. A separate encrypted-only PR and DR gate follow.
No WAF, CA associations, DNS, Access, Tunnel, TrueNAS or HAOS writes.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
import getpass
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import pwd
import re
import stat
import subprocess
import sys
import urllib.error
import urllib.request
import warnings

from ansible.parsing.vault import VaultLib, VaultSecret
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
import yaml

ROOT = Path(__file__).resolve().parents[1]
FLEET_ROOT = Path(os.environ.get("DH_FLEET_DETAIL_DIR", "/var/lib/ansimble/fleet-detail"))
STAGE_BASE = Path("/dev/shm")
VAULT = FLEET_ROOT / "secrets/fleet.vault.yml"
TOKEN = Path("/root/.config/digitalhouses/cloudflare/api-token.secret")
HEADER = b"$ANSIBLE_VAULT;1.1;AES256\n"
API = "https://api.cloudflare.com/client/v4"
SITE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
HEX64 = re.compile(r"^[a-f0-9]{64}$")
HEX32 = re.compile(r"^[a-f0-9]{32}$")
UUID = re.compile(r"^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$")


class Block(Exception):
    pass


def plan_module():
    file = ROOT / "bin/ha-mtls-certificate-plan.py"
    spec = importlib.util.spec_from_file_location("mtls_certificate_plan", file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def validate_state(site):
    if not SITE.fullmatch(site):
        raise Block("SITE_INVALID")
    path = FLEET_ROOT / "inventories" / site / "cloudflare_ha_mtls.yml"
    obj = yaml.safe_load(path.read_text())
    state = obj.get("cloudflare_ha_mtls") if isinstance(obj, dict) else None
    if not isinstance(state, dict) or state.get("site_id") != site:
        raise Block("INVENTORY_SITE_ID_MISMATCH")
    # These gates remain off even during issuance: route and WAF are separate.
    if (state.get("enabled") is not False
            or state.get("waf", {}).get("apply_authorized") is not False
            or state.get("cert_key_storage") != "encrypted-fleet-vault"
            or state.get("cert_issue_strategy") != "csr-generated-locally"
            or state.get("cert_subject") != "CN-only-exact-hostname"
            or state.get("trust_scope") != "Cloudflare-managed-CA"):
        raise Block("INVENTORY_SAFETY_GUARD")
    if not re.fullmatch(r"[a-z0-9-]+-ha-mtls\.digitalhouses\.vip",
                        str(state.get("hostname", ""))):
        raise Block("MTLS_HOSTNAME_INVALID")
    if not re.fullmatch(r"[a-f0-9]{32}", str(state.get("cf_zone_id", ""))):
        raise Block("CF_ZONE_ID_INVALID")
    if not re.fullmatch(r"[a-f0-9]{32}", str(state.get("cf_account_id", ""))):
        raise Block("CF_ACCOUNT_ID_INVALID")
    days = state.get("cert_validity_days")
    if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= 3650:
        raise Block("CERT_VALIDITY_INVALID")
    return state


def git_as_runner(*args):
    p = subprocess.run(
        ["runuser", "-u", "ansimble", "--", "git", "-C", str(FLEET_ROOT), *args],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        text=True, timeout=90, check=False,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
    )
    if p.returncode != 0:
        raise Block("GIT_CHECK_FAILED")
    return p.stdout.strip()


def root_and_repository(expected_sha, site):
    if (not SITE.fullmatch(site) or os.geteuid() != 0
            or os.uname().nodename != "ansimble-" + site):
        raise Block("REQUIRES_SITE_RUNNER_ROOT")
    if git_as_runner("branch", "--show-current") != "main":
        raise Block("GIT_NOT_MAIN")
    if git_as_runner("status", "--porcelain", "--untracked-files=all"):
        raise Block("GIT_NOT_CLEAN")
    git_as_runner("fetch", "--quiet", "origin", "main")
    if git_as_runner("rev-parse", "HEAD") != git_as_runner("rev-parse", "origin/main"):
        raise Block("GIT_MAIN_NOT_SYNCHRONIZED")
    if not HEX64.fullmatch(expected_sha):
        raise Block("EXPECTED_VAULT_SHA_INVALID")
    if VAULT.is_symlink() or not VAULT.is_file():
        raise Block("VAULT_FILE_INVALID")
    st = VAULT.stat()
    if (st.st_uid != pwd.getpwnam("ansimble").pw_uid
            or stat.S_IMODE(st.st_mode) != 0o600):
        raise Block("VAULT_FILE_PERMISSIONS_INVALID")
    encrypted = VAULT.read_bytes()
    if (not encrypted.startswith(HEADER)
            or hashlib.sha256(encrypted).hexdigest() != expected_sha):
        raise Block("GITHUB_VAULT_HASH_OR_FORMAT_CHANGED")
    return encrypted


def master_input():
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            master = getpass.getpass("Existing Fleet Vault master password: ")
    except (EOFError, OSError, getpass.GetPassWarning):
        raise Block("SECURE_MASTER_INPUT_REQUIRED") from None
    if len(master) < 8 or "\n" in master or "\r" in master:
        raise Block("MASTER_INVALID")
    return master.encode()


def prepared(encrypted, master, state, generation):
    crypt = VaultLib([("default", VaultSecret(master))])
    try:
        payload = yaml.safe_load(crypt.decrypt(encrypted))
    except Exception:
        raise Block("VAULT_DECRYPT_FAILED") from None
    site = (((payload or {}).get("fleet_secrets") or {}).get("sites") or {}).get(
        state["site_id"]
    )
    mtls = site.get("mtls") if isinstance(site, dict) else None
    versions = mtls.get("generations") if isinstance(mtls, dict) else None
    if (not isinstance(versions, list) or len(versions) != 1
            or mtls.get("schema_version") != 1):
        raise Block("MTLS_VAULT_NAMESPACE_UNEXPECTED")
    entry = versions[0]
    if (not isinstance(entry, dict)
            or entry.get("status") != "prepared"
            or entry.get("generation_id") != generation
            or not HEX32.fullmatch(generation)
            or entry.get("hostname") != state["hostname"]
            or entry.get("validity_days") != state["cert_validity_days"]
            or entry.get("certificate_pem") is not None
            or entry.get("cloudflare_certificate_id") is not None
            or not isinstance(entry.get("p12_password"), str)
            or len(entry["p12_password"]) < 32):
        raise Block("MTLS_PREPARED_GENERATION_DRIFT")
    try:
        key = serialization.load_pem_private_key(
            entry["private_key_pem"].encode(), password=None
        )
        csr = x509.load_pem_x509_csr(entry["csr_pem"].encode())
        if (not isinstance(key, rsa.RSAPrivateKey) or key.key_size != 2048
                or csr.subject != x509.Name([
                    x509.NameAttribute(NameOID.COMMON_NAME, state["hostname"])
                ])
                or not csr.is_signature_valid
                or key.public_key().public_numbers() != csr.public_key().public_numbers()):
            raise Block("PREPARED_PRIVATE_KEY_CSR_MISMATCH")
    except (TypeError, ValueError, KeyError, AttributeError):
        raise Block("PREPARED_KEY_CSR_INVALID") from None
    return crypt, payload, entry, csr


def cert_public_key(certificate_pem):
    try:
        cert = x509.load_pem_x509_certificate(certificate_pem.encode())
        return cert.public_key().public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    except (AttributeError, TypeError, ValueError):
        raise Block("REMOTE_CERT_PEM_INVALID") from None


def verify_issued(record, csr, hostname, days):
    if not isinstance(record, dict):
        raise Block("CF_CERT_RESPONSE_INVALID")
    cert_pem = record.get("certificate")
    ca = record.get("certificate_authority") or {}
    certid = record.get("id")
    if (not isinstance(certid, str) or not UUID.fullmatch(certid)
            or record.get("status") != "active"
            or not isinstance(cert_pem, str)
            or "Cloudflare Managed CA" not in str(ca.get("name", ""))):
        raise Block("CF_CERT_ID_STATUS_OR_MANAGED_CA_INVALID")
    cert = x509.load_pem_x509_certificate(cert_pem.encode())
    expected_subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hostname)])
    if cert.subject != expected_subject:
        raise Block("CF_ISSUED_SUBJECT_NOT_EXACT_CN")
    if cert.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    ) != csr.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    ):
        raise Block("CF_ISSUED_PUBLIC_KEY_MISMATCH")
    if record.get("common_name") not in (None, hostname):
        raise Block("CF_ISSUED_API_COMMON_NAME_MISMATCH")
    if record.get("validity_days") not in (None, days):
        raise Block("CF_ISSUED_VALIDITY_DAYS_MISMATCH")
    fingerprint = cert.fingerprint(hashes.SHA256()).hex()
    remote_fingerprint = str(record.get("fingerprint_sha256", "")).replace(":", "").lower()
    if remote_fingerprint and remote_fingerprint != fingerprint:
        raise Block("CF_ISSUED_FINGERPRINT_MISMATCH")
    now = datetime.now(timezone.utc)
    if not cert.not_valid_before_utc <= now <= cert.not_valid_after_utc:
        raise Block("CF_CERT_NOT_CURRENTLY_VALID")
    duration = cert.not_valid_after_utc - cert.not_valid_before_utc
    if not timedelta(days=days-1) <= duration <= timedelta(days=days+1):
        raise Block("CF_ISSUED_CERT_VALIDITY_UNEXPECTED")
    return {
        "certificate_pem": cert_pem,
        "cloudflare_certificate_id": certid,
        "fingerprint_sha256": fingerprint,
        "issued_on": record.get("issued_on"),
        "expires_on": record.get("expires_on"),
    }


class CloudflareAPI:
    def __init__(self, token):
        self.token = token

    def request(self, method, path, data=None):
        if method not in ("GET", "POST") or not path.startswith("/"):
            raise Block("CF_REQUEST_UNEXPECTED")
        body = json.dumps(data).encode() if data is not None else None
        if method == "POST" and (not path.endswith("/client_certificates")
                                 or set(data or {}) != {"csr", "validity_days"}):
            raise Block("CF_POST_SCOPE_OR_BODY_INVALID")
        req = urllib.request.Request(
            API + path, method=method, data=body,
            headers={"Authorization": "Bearer " + self.token, "Accept": "application/json",
                     "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                result = json.load(response)
        except urllib.error.HTTPError as exc:
            if method == "POST":
                raise Block(f"CF_POST_HTTP_{exc.code}_RECONCILE_BEFORE_RETRY") from None
            raise Block(f"CF_GET_HTTP_{exc.code}") from None
        except (TimeoutError, OSError, ValueError):
            raise Block("CF_POST_OUTCOME_UNKNOWN_RECONCILE" if method == "POST"
                        else "CF_GET_TRANSPORT_FAILED") from None
        if not isinstance(result, dict) or result.get("success") is not True:
            raise Block("CF_POST_OUTCOME_UNKNOWN_RECONCILE" if method == "POST"
                        else "CF_GET_NOT_SUCCESSFUL")
        return result.get("result")

    def get(self, path):
        return self.request("GET", path)


def reconcile_existing(api, zone, csr):
    """Identify any existing certificate by CSR or matching certificate public key."""
    key_der = csr.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    matches = []
    for page in range(1, 4):
        listed = api.get(
            f"/zones/{zone}/client_certificates?status=all&per_page=50&page={page}"
        )
        if not isinstance(listed, list):
            raise Block("CERT_LIST_INVALID")
        for item in listed:
            cert_id = item.get("id")
            if not isinstance(cert_id, str) or not UUID.fullmatch(cert_id):
                raise Block("CERT_LIST_ID_UNEXPECTED")
            details = api.get(f"/zones/{zone}/client_certificates/{cert_id}")
            if not isinstance(details, dict):
                raise Block("CERT_DETAILS_INVALID")
            if details.get("certificate") is None:
                raise Block("CERT_DETAILS_MISSING_PEM")
            if cert_public_key(details["certificate"]) == key_der:
                if details.get("status") != "active":
                    raise Block("MATCHING_CERTIFICATE_IS_NOT_ACTIVE")
                matches.append(details)
        if len(listed) < 50:
            break
    else:
        raise Block("CERTIFICATE_PAGINATION_LIMIT")
    if len(matches) > 1:
        raise Block("DUPLICATE_CERTIFICATES_SHARE_PREPARED_KEY")
    return matches[0] if matches else None


def stage_encrypted(candidate, state, generation, before_sha, certificate):
    """Only encrypted data and nonsecret metadata; separate PR is mandatory."""
    directory = STAGE_BASE / ("dh-mtls-" + state["site_id"] + "-issued-stage")
    if directory.exists() or directory.is_symlink():
        raise Block("ISSUED_STAGE_ALREADY_EXISTS")
    fs = subprocess.run(
        ["findmnt", "-T", str(STAGE_BASE), "-n", "-o", "FSTYPE"],
        capture_output=True, text=True, check=False,
    )
    if fs.returncode != 0 or fs.stdout.strip() != "tmpfs":
        raise Block("ISSUED_STAGE_REQUIRES_TMPFS")
    directory.mkdir(mode=0o700)
    output = directory / "fleet.vault.yml"
    manifest_path = directory / "manifest.json"
    manifest = {
        "site": state["site_id"], "hostname": state["hostname"],
        "generation_id": generation, "source_vault_sha256": before_sha,
        "candidate_vault_sha256": hashlib.sha256(candidate).hexdigest(),
        "certificate_id": certificate["cloudflare_certificate_id"],
        "certificate_fingerprint_sha256": certificate["fingerprint_sha256"],
        "state": "ISSUED_BUT_ENCRYPTED_GITHUB_PR_REQUIRED",
    }
    try:
        for path, data in (
            (output, candidate),
            (manifest_path, (json.dumps(manifest) + "\n").encode()),
        ):
            with path.open("xb") as handle:
                os.fchmod(handle.fileno(), 0o600)
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
        if not output.read_bytes().startswith(HEADER):
            raise Block("ISSUED_STAGE_ENCRYPTED_HEADER_INVALID")
        # Only ciphertext and public manifest may be transferred to GitHub.
        # Grant the unprivileged read-only Git identity access to this private
        # tmpfs directory without exposing plaintext keys or the master.
        if os.geteuid() == 0:
            identity = pwd.getpwnam("ansimble")
            for path in (output, manifest_path):
                os.chown(path, identity.pw_uid, identity.pw_gid)
            os.chown(directory, identity.pw_uid, identity.pw_gid)
    except Exception:
        output.unlink(missing_ok=True)
        manifest_path.unlink(missing_ok=True)
        directory.rmdir()
        raise
    return manifest


def encrypted_issued_candidate(crypt, payload, entry, record, csr, state):
    values = verify_issued(record, csr, state["hostname"], state["cert_validity_days"])
    entry.update(values)
    entry["status"] = "issued_unpublished"
    serial = yaml.safe_dump(payload, sort_keys=False, allow_unicode=True).encode()
    ciphertext = crypt.encrypt(serial)
    if (not ciphertext.startswith(HEADER) or crypt.decrypt(ciphertext) != serial):
        raise Block("ISSUED_VAULT_ENCRYPTION_VERIFICATION_FAILED")
    return ciphertext, values


def authorize_one_prepared_certificate(state, expected_sha, generation, hostname):
    """Authorize a single recoverable CSR, never general fleet writes.

    The approval is narrowly scoped, committed through GitHub CI and expires
    automatically. Having this approval does not enable DNS/WAF/CA writes.
    """
    if state.get("live_writes_authorized") is not False:
        raise Block("GLOBAL_FLEET_WRITES_MUST_STAY_DISABLED")
    approval = state.get("certificate_issue")
    required = {
        "apply_authorized", "generation_id", "expected_vault_sha256",
        "hostname", "validity_days", "approved_until_utc",
    }
    if (not isinstance(approval, dict) or set(approval) != required
            or approval.get("apply_authorized") is not True
            or approval.get("generation_id") != generation
            or approval.get("expected_vault_sha256") != expected_sha
            or approval.get("hostname") != hostname
            or approval.get("validity_days") != state["cert_validity_days"]):
        raise Block("CERTIFICATE_ISSUANCE_NOT_APPROVED_FOR_THIS_GENERATION")
    try:
        expiration = datetime.fromisoformat(
            approval["approved_until_utc"].replace("Z", "+00:00")
        )
    except (ValueError, TypeError, AttributeError):
        raise Block("CERTIFICATE_ISSUANCE_APPROVAL_TIME_INVALID") from None
    now = datetime.now(timezone.utc)
    if (expiration.tzinfo is None or expiration <= now
            or expiration > now + timedelta(hours=24)):
        raise Block("CERTIFICATE_ISSUANCE_APPROVAL_EXPIRED_OR_TOO_BROAD")
    return True


def run(site, expected_sha, generation, expected_hostname, issue_once):
    state = validate_state(site)
    if expected_hostname != state["hostname"]:
        raise Block("CONFIRMED_HOSTNAME_DIFFERS")
    # The global write gate remains OFF; only this prepared CSR is approved.
    if issue_once:
        authorize_one_prepared_certificate(
            state, expected_sha, generation, expected_hostname
        )
    encrypted = root_and_repository(expected_sha, site)
    if TOKEN.is_symlink() or not TOKEN.is_file() or TOKEN.stat().st_uid != 0:
        raise Block("CLOUDFLARE_TOKEN_NOT_ROOT_ONLY")
    if stat.S_IMODE(TOKEN.stat().st_mode) != 0o600:
        raise Block("CLOUDFLARE_TOKEN_MODE_INVALID")
    token = TOKEN.read_text().strip()
    crypt, payload, entry, csr = prepared(encrypted, master_input(), state, generation)
    print("MODE=" + ("ISSUE_ONE_EXPLICITLY_AUTHORIZED" if issue_once else "READ_ONLY_ISSUANCE_PREFLIGHT"))
    print("SITE=" + site)
    print("CSR_CN=CN=" + state["hostname"])
    print("GENERATION_ID=" + generation)
    plan = plan_module()
    api = CloudflareAPI(token)
    # API wrapper exposes GET, accepted by the baseline planner.
    status = plan.build_plan(state, api)
    if (status["home_active_certificates"] != 0 or status["mtls_tunnel_route_present"]
            or status["ca_hostname_associated"]):
        raise Block("CERTIFICATE_ENROLLMENT_STAGE_DRIFT")
    print("CLOUDFLARE_BASELINE=PASS")
    found = reconcile_existing(api, state["cf_zone_id"], csr)
    print("MATCHING_REMOTE_CERTIFICATE=" + ("PRESENT" if found else "ABSENT"))
    if not issue_once:
        print("CF_WRITES=NONE")
        return
    if (STAGE_BASE / ("dh-mtls-" + site + "-issued-stage")).exists():
        raise Block("ISSUED_STAGE_ALREADY_EXISTS")
    if found is None:
        found = api.request(
            "POST", f"/zones/{state['cf_zone_id']}/client_certificates",
            {"csr": entry["csr_pem"], "validity_days": state["cert_validity_days"]},
        )
        print("CERTIFICATE_CREATED=YES")
    else:
        print("CERTIFICATE_CREATED=NO_RECONCILED_EXISTING")
    ciphertext, verified = encrypted_issued_candidate(
        crypt, payload, entry, found, csr, state
    )
    manifest = stage_encrypted(
        ciphertext, state, generation, expected_sha, verified
    )
    print("ISSUED_CERTIFICATE_X509_VALIDATION=PASS")
    print("NEW_ENCRYPTED_VAULT_SHA256=" + manifest["candidate_vault_sha256"])
    print("ENCRYPTED_GITHUB_PR_REQUIRED=YES")
    print("WAF_DNS_ACCESS_TUNNEL_TRUENAS_MUTATIONS=NONE")


if __name__ == "__main__":
    try:
        import argparse
        parser = argparse.ArgumentParser()
        parser.add_argument("--site", required=True)
        parser.add_argument("--expected-vault-sha256", required=True)
        parser.add_argument("--generation-id", required=True)
        parser.add_argument("--confirm-hostname", required=True)
        group = parser.add_mutually_exclusive_group(required=True)
        group.add_argument("--preflight", action="store_true")
        group.add_argument("--issue-once", action="store_true")
        args = parser.parse_args()
        run(args.site, args.expected_vault_sha256, args.generation_id,
            args.confirm_hostname, args.issue_once)
    except Block as e:
        print("BLOCK=" + str(e), file=sys.stderr)
        sys.exit(2)
    except Exception as e:
        print("BLOCK=UNEXPECTED_" + type(e).__name__, file=sys.stderr)
        sys.exit(2)
    finally:
        print("=== DONE ===")
        print("=== TERMINAL STAYS OPEN ===")
