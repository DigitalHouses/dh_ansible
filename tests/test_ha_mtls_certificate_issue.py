"""Offline synthetic tests for single-certificate Cloudflare Managed-CA issuance."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
from pathlib import Path

from ansible.parsing.vault import VaultLib, VaultSecret
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
ISSUER = ROOT / "bin/ha-mtls-certificate-issue.py"
SITE = "sample-home"
HOST = "sample-home-ha-mtls.digitalhouses.vip"
GENERATION = "a" * 32
CERT_ID = "12345678-1234-1234-1234-123456789abc"
MASTER = b"synthetic-test-master-only-not-real"


def load():
    spec = importlib.util.spec_from_file_location("mtls_certificate_issue", ISSUER)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def material(host=HOST, days=365, different_key=False, expired=False):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, host)])
    csr = x509.CertificateSigningRequestBuilder().subject_name(name).sign(
        key, hashes.SHA256()
    )
    now = datetime.now(timezone.utc)
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Cloudflare Managed CA for account")])
    start = now - (timedelta(days=days+2) if expired else timedelta(minutes=1))
    end = start + timedelta(days=days)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(ca)
        .public_key(
            rsa.generate_private_key(public_exponent=65537, key_size=2048).public_key()
            if different_key else key.public_key()
        )
        .serial_number(x509.random_serial_number())
        .not_valid_before(start)
        .not_valid_after(end)
        .sign(ca_key, hashes.SHA256())
    )
    return (
        key.private_bytes(serialization.Encoding.PEM,
                          serialization.PrivateFormat.PKCS8,
                          serialization.NoEncryption()).decode(),
        csr,
        {
            "id": CERT_ID,
            "certificate": cert.public_bytes(serialization.Encoding.PEM).decode(),
            "certificate_authority": {"name": "Cloudflare Managed CA for account"},
            "status": "active",
            "common_name": host,
            "validity_days": days,
            "fingerprint_sha256": cert.fingerprint(hashes.SHA256()).hex(),
            "issued_on": start.isoformat(),
            "expires_on": end.isoformat(),
        },
    )


def synthetic_state():
    """Minimal nonproduction policy for offline certificate-state guards."""
    return {
        "site_id": SITE,
        "hostname": HOST,
        "cert_validity_days": 365,
        "live_writes_authorized": False,
        "waf": {"apply_authorized": False},
        "certificate_issue": {"apply_authorized": False},
    }


def encrypted_prepared(key_pem, csr_pem):
    data = {
        "fleet_secrets": {
            "sites": {
                SITE: {
                    "pve": {"audit_token_secret": "fake"},
                    "mtls": {
                        "schema_version": 1,
                        "generations": [{
                            "generation_id": GENERATION,
                            "status": "prepared",
                            "hostname": HOST,
                            "validity_days": 365,
                            "private_key_pem": key_pem,
                            "csr_pem": csr_pem,
                            "p12_password": "synthetic-password-32-character-minimum-0000",
                            "certificate_pem": None,
                            "cloudflare_certificate_id": None,
                        }],
                    },
                },
            },
        },
    }
    crypt = VaultLib([("default", VaultSecret(MASTER))])
    return crypt.encrypt(yaml.safe_dump(data).encode()), crypt


def test_prepared_rejects_wrong_master_wrong_generation():
    m = load()
    key, csr, _ = material()
    ciphertext, _ = encrypted_prepared(
        key, csr.public_bytes(serialization.Encoding.PEM).decode()
    )
    state = synthetic_state()
    crypt, data, entry, recovered_csr = m.prepared(ciphertext, MASTER, state, GENERATION)
    assert recovered_csr.subject == x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, HOST)
    ])
    assert entry["status"] == "prepared"
    with pytest.raises(m.Block, match="VAULT_DECRYPT_FAILED"):
        m.prepared(ciphertext, b"incorrect-synthetic-master", state, GENERATION)
    with pytest.raises(m.Block, match="MTLS_PREPARED_GENERATION_DRIFT"):
        m.prepared(ciphertext, MASTER, state, "0" * 32)


def test_issuer_x509_verification_passes_exact_host_ca_key_sha256():
    m = load()
    _, csr, record = material()
    result = m.verify_issued(record, csr, HOST, 365)
    assert result["cloudflare_certificate_id"] == CERT_ID
    assert result["fingerprint_sha256"] == record["fingerprint_sha256"]
    assert "BEGIN CERTIFICATE" in result["certificate_pem"]


@pytest.mark.parametrize("defect,reason", [
    ("wrong_ca", "MANAGED_CA_INVALID"),
    ("wrong_common_name_metadata", "API_COMMON_NAME_MISMATCH"),
    ("wrong_fingerprint", "FINGERPRINT_MISMATCH"),
    ("revoked", "ID_STATUS_OR_MANAGED_CA_INVALID"),
    ("wrong_validity", "VALIDITY_DAYS_MISMATCH"),
])
def test_issuer_rejects_untrusted_cf_metadata(defect, reason):
    m = load()
    _, csr, record = material()
    if defect == "wrong_ca":
        record["certificate_authority"] = {"name": "Untrusted"}
    elif defect == "wrong_common_name_metadata":
        record["common_name"] = "different-house"
    elif defect == "wrong_fingerprint":
        record["fingerprint_sha256"] = "0" * 64
    elif defect == "revoked":
        record["status"] = "revoked"
    elif defect == "wrong_validity":
        record["validity_days"] = 100
    with pytest.raises(m.Block, match=reason):
        m.verify_issued(record, csr, HOST, 365)


def test_x509_exact_subject_and_public_key_guards():
    m = load()
    _, csr, wrong_cn = material(host="other-house-ha-mtls.digitalhouses.vip")
    _, original_csr, wrong_public_key = material(different_key=True)
    with pytest.raises(m.Block, match="SUBJECT_NOT_EXACT_CN"):
        m.verify_issued(wrong_cn, csr, HOST, 365)
    with pytest.raises(m.Block, match="PUBLIC_KEY_MISMATCH"):
        m.verify_issued(wrong_public_key, original_csr, HOST, 365)
    _, csr, expired = material(expired=True)
    with pytest.raises(m.Block, match="NOT_CURRENTLY_VALID"):
        m.verify_issued(expired, csr, HOST, 365)


def test_reconcile_detects_existing_cert_and_prevents_duplicates():
    m = load()
    _, csr, record = material()
    zone = "f" * 32
    list_path = f"/zones/{zone}/client_certificates?status=all&per_page=50&page=1"
    details_path = f"/zones/{zone}/client_certificates/{CERT_ID}"

    class Stub:
        def __init__(self):
            self.events = []

        def get(self, path):
            self.events.append(path)
            return [record] if path == list_path else record

    api = Stub()
    assert m.reconcile_existing(api, zone, csr)["id"] == CERT_ID
    assert api.events == [list_path, details_path]
    assert m.reconcile_existing(Stub(), zone, material()[1]) is None

    class Duplicate:
        def get(self, path):
            return [record, record] if path == list_path else record

    with pytest.raises(m.Block, match="DUPLICATE_CERTIFICATES"):
        m.reconcile_existing(Duplicate(), zone, csr)


def test_issued_encrypted_stage_has_no_plaintext(tmp_path, monkeypatch):
    m = load()
    key, csr, record = material()
    encoded, _ = encrypted_prepared(
        key, csr.public_bytes(serialization.Encoding.PEM).decode()
    )
    state = synthetic_state()
    crypt, payload, entry, validated = m.prepared(encoded, MASTER, state, GENERATION)
    candidate, values = m.encrypted_issued_candidate(
        crypt, payload, entry, record, validated, state
    )
    assert candidate.startswith(m.HEADER)
    assert key.encode() not in candidate
    assert record["certificate"].encode() not in candidate
    assert yaml.safe_load(crypt.decrypt(candidate))["fleet_secrets"]["sites"][SITE]["mtls"]["generations"][0]["status"] == "issued_unpublished"
    monkeypatch.setattr(m, "STAGE_BASE", tmp_path)
    # The synthetic staging test does not need to chown to a system account.
    monkeypatch.setattr(m.os, "geteuid", lambda: 1001)
    # Unit tests emulate the host tmpfs contract without mutating production.
    from subprocess import CompletedProcess
    monkeypatch.setattr(m.subprocess, "run", lambda *a, **kw: CompletedProcess(a, 0, "tmpfs\n", ""))
    meta = m.stage_encrypted(candidate, state, GENERATION, "0" * 64, values)
    folder = tmp_path / "dh-mtls-sample-home-issued-stage"
    assert meta["candidate_vault_sha256"] == hashlib.sha256(candidate).hexdigest()
    assert (folder / "fleet.vault.yml").read_bytes() == candidate
    assert (folder / "fleet.vault.yml").stat().st_mode & 0o777 == 0o600
    assert folder.stat().st_mode & 0o777 == 0o700
    with pytest.raises(m.Block, match="ISSUED_STAGE_ALREADY_EXISTS"):
        m.stage_encrypted(candidate, state, GENERATION, "0" * 64, values)


def test_one_time_authorization_is_disabled_after_issuance(monkeypatch):
    m = load()
    monkeypatch.setattr(m, "validate_state", lambda site: synthetic_state())
    current = synthetic_state()
    assert current["live_writes_authorized"] is False
    assert current["waf"]["apply_authorized"] is False
    assert current["certificate_issue"]["apply_authorized"] is False
    with pytest.raises(m.Block, match="CERTIFICATE_ISSUANCE_NOT_APPROVED_FOR_THIS_GENERATION"):
        m.authorize_one_prepared_certificate(
            current, "2" * 64,
            GENERATION, HOST
        )
    with pytest.raises(m.Block, match="NOT_APPROVED_FOR_THIS_GENERATION"):
        m.run(SITE, "0" * 64, GENERATION, HOST, issue_once=True)


def test_future_approval_must_remain_generation_specific_and_expiring():
    m = load()
    current = synthetic_state()
    until = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()
    grant = {
        "apply_authorized": True,
        "generation_id": GENERATION,
        "expected_vault_sha256": "1" * 64,
        "hostname": HOST,
        "validity_days": 365,
        "approved_until_utc": until,
    }
    state = {**current, "certificate_issue": grant}
    assert m.authorize_one_prepared_certificate(
        state, "1" * 64, GENERATION, HOST
    ) is True
    for sha, generation, hostname in (
        ("0" * 64, GENERATION, HOST),
        ("1" * 64, "0" * 32, HOST),
        ("1" * 64, GENERATION, "wrong-ha-mtls.digitalhouses.vip"),
    ):
        with pytest.raises(m.Block, match="NOT_APPROVED_FOR_THIS_GENERATION"):
            m.authorize_one_prepared_certificate(state, sha, generation, hostname)
    with pytest.raises(m.Block, match="GLOBAL_FLEET_WRITES_MUST_STAY_DISABLED"):
        m.authorize_one_prepared_certificate(
            {**state, "live_writes_authorized": True}, "1" * 64, GENERATION, HOST
        )
    with pytest.raises(m.Block, match="APPROVAL_EXPIRED_OR_TOO_BROAD"):
        m.authorize_one_prepared_certificate(
            {**state, "certificate_issue": {**grant, "approved_until_utc": "2026-10-09T12:00:00Z"}},
            "1" * 64, GENERATION, HOST
        )
    with pytest.raises(m.Block, match="NOT_APPROVED_FOR_THIS_GENERATION"):
        m.authorize_one_prepared_certificate(
            {**state, "certificate_issue": {**grant, "approve_everything": True}},
            "1" * 64, GENERATION, HOST
        )


def test_api_rejects_noncertificate_post_and_strips_api_error_bodies(monkeypatch):
    m = load()
    api = m.CloudflareAPI("not-a-real-token")
    with pytest.raises(m.Block, match="CF_POST_SCOPE_OR_BODY_INVALID"):
        api.request("POST", "/zones/" + "f" * 32 + "/dns_records",
                    {"csr": "x", "validity_days": 365})
    with pytest.raises(m.Block, match="CF_REQUEST_UNEXPECTED"):
        api.request("DELETE", "/zones/example")

    class BadResponse:
        code = 500

    def http_err(_req, timeout=30):
        raise m.urllib.error.HTTPError("https://api.cloudflare.com",
                                       500, "private secret error",
                                       {}, None)
    monkeypatch.setattr(m.urllib.request, "urlopen", http_err)
    with pytest.raises(m.Block, match="CF_POST_HTTP_500_RECONCILE_BEFORE_RETRY"):
        api.request("POST", "/zones/" + "f" * 32 + "/client_certificates",
                    {"csr": "test synthetic only", "validity_days": 365})
