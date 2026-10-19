"""Offline tests for issued mTLS X509 recovery from the existing Fleet Vault."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
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
HOST = "sample-home-ha-mtls.digitalhouses.vip"
MASTER = b"synthetic-master-not-a-real-password"
CERT_ID = "12345678-1234-1234-1234-123456789abc"


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    obj = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obj)
    return obj


def synthetic_issued(cn=HOST, signed_for_other_key=False):
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
    csr = x509.CertificateSigningRequestBuilder().subject_name(subject).sign(
        private, hashes.SHA256()
    )
    ca = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.now(timezone.utc)
    key_for_cert = (
        rsa.generate_private_key(public_exponent=65537, key_size=2048).public_key()
        if signed_for_other_key else private.public_key()
    )
    cert = (x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "synthetic Cloudflare Managed CA")]))
        .public_key(key_for_cert)
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now - timedelta(minutes=1) + timedelta(days=365))
        .sign(ca, hashes.SHA256()))
    key_pem = private.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()
    ).decode()
    csr_pem = csr.public_bytes(serialization.Encoding.PEM).decode()
    cert_pem = cert.public_bytes(serialization.Encoding.PEM).decode()
    fingerprint = cert.fingerprint(hashes.SHA256()).hex()
    entry = {
        "generation_id": "a" * 32,
        "status": "issued_unpublished",
        "hostname": HOST,
        "created_at_utc": "2026-10-10T00:00:00+00:00",
        "validity_days": 365,
        "private_key_pem": key_pem,
        "csr_pem": csr_pem,
        "p12_password": "synthetic-only-p12-password-over-32-characters",
        "certificate_pem": cert_pem,
        "cloudflare_certificate_id": CERT_ID,
        "fingerprint_sha256": fingerprint,
    }
    doc = {
        "fleet_secrets": {
            "sites": {
                "sample-home": {
                    "pve": {"audit": "test"},
                    "truenas": {"audit": "test"},
                    "cloudflare": {"audit": "test"},
                    "mtls": {"schema_version": 1, "generations": [entry]},
                },
            },
        },
    }
    vault = VaultLib([("default", VaultSecret(MASTER))])
    cipher = vault.encrypt(yaml.safe_dump(doc).encode())
    return cipher, doc, fingerprint


def test_issued_identity_recovered_and_x509_corresponds():
    m = load("bin/ha-mtls-vault-verify.py", "mtls_recovery_issued_ok")
    cipher, _, fp = synthetic_issued()
    host, key, csr, generation, entry = m.recovery_identity(
        cipher, MASTER, "sample-home", "issued_unpublished"
    )
    assert host == HOST and generation == "a" * 32
    assert m.verify_issued_identity(host, key, csr, entry, fp) == CERT_ID
    with pytest.raises(m.Block, match="RECOVERY_MTLS_PREPARED_STATE_INVALID"):
        m.prepared_identity(cipher, MASTER, "sample-home")


@pytest.mark.parametrize("mutation,error", [
    ({"status": "prepared"}, "RECOVERY_MTLS_PREPARED_STATE_INVALID"),
    ({"cloudflare_certificate_id": None}, "RECOVERY_ISSUED_CERT_METADATA_INVALID"),
    ({"fingerprint_sha256": "0" * 64}, "RECOVERY_ISSUED_CERT_METADATA_INVALID"),
    ({"certificate_pem": "bad PEM"}, "RECOVERY_ISSUED_CERT_METADATA_INVALID"),
    ({"hostname": "someone-else.example"}, "RECOVERY_MTLS_HOST_INVALID"),
])
def test_recovery_rejects_corrupt_issued_metadata(mutation,error):
    m = load("bin/ha-mtls-vault-verify.py", "mtls_recovery_corrupt")
    _, original, fingerprint = synthetic_issued()
    original["fleet_secrets"]["sites"]["sample-home"]["mtls"]["generations"][0].update(mutation)
    vault = VaultLib([("default", VaultSecret(MASTER))])
    cipher = vault.encrypt(yaml.safe_dump(original).encode())
    if error.startswith("RECOVERY_MTLS"):
        with pytest.raises(m.Block, match=error):
            m.recovery_identity(cipher, MASTER, "sample-home", "issued_unpublished")
    else:
        host, key, csr, _, entry = m.recovery_identity(
            cipher, MASTER, "sample-home", "issued_unpublished"
        )
        with pytest.raises(m.Block, match=error):
            m.verify_issued_identity(host, key, csr, entry, fingerprint)


def test_cross_site_certificate_and_wrong_key_rejected():
    m = load("bin/ha-mtls-vault-verify.py", "mtls_recovery_wrongkeys")
    cipher, _, fp = synthetic_issued(cn="wrong-ha-mtls.digitalhouses.vip")
    h, key, csr, _, entry = m.recovery_identity(
        cipher, MASTER, "sample-home", "issued_unpublished"
    )
    with pytest.raises(m.Block, match="RECOVERY_ISSUED_CERT_CN_INVALID"):
        m.verify_issued_identity(h, key, csr, entry, fp)
    cipher, _, fp = synthetic_issued(signed_for_other_key=True)
    h, key, csr, _, entry = m.recovery_identity(
        cipher, MASTER, "sample-home", "issued_unpublished"
    )
    with pytest.raises(m.Block, match="RECOVERY_ISSUED_CERT_KEY_MISMATCH"):
        m.verify_issued_identity(h, key, csr, entry, fp)


def test_untrusted_external_fingerprint_pin_fails_closed(monkeypatch):
    m = load("bin/ha-mtls-vault-verify.py", "mtls_recovery_wrong_pin")
    monkeypatch.setattr(m, "safe_runner", lambda site: None)
    cipher, _, fp = synthetic_issued()
    h, key, csr, _, entry = m.recovery_identity(
        cipher, MASTER, "sample-home", "issued_unpublished"
    )
    with pytest.raises(m.Block, match="RECOVERY_ISSUED_CERT_METADATA_INVALID"):
        m.verify_issued_identity(h, key, csr, entry, "0" * 64)
    with pytest.raises(m.Block, match="RECOVERY_EXPECTED_CERT_FINGERPRINT_INVALID"):
        m.verify_issued_identity(h, key, csr, entry, "not-hex")
    with pytest.raises(m.Block, match="RECOVERY_ISSUED_FINGERPRINT_PIN_REQUIRED"):
        m.run("sample-home", "a" * 64, "issued_unpublished", None)


def test_encrypted_recovery_never_logs_pem_or_p12_password(capsys):
    m = load("bin/ha-mtls-vault-verify.py", "mtls_recovery_no_secrets")
    cipher, original, fp = synthetic_issued()
    h, key, csr, _, entry = m.recovery_identity(
        cipher, MASTER, "sample-home", "issued_unpublished"
    )
    assert m.verify_issued_identity(h, key, csr, entry, fp) == CERT_ID
    stdout = capsys.readouterr()
    assert stdout.out == "" and stdout.err == ""
    assert key.encode() not in cipher
    assert original["fleet_secrets"]["sites"]["sample-home"]["mtls"]["generations"][0]["certificate_pem"].encode() not in cipher
