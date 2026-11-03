"""No task70 Cloudflare certificate writes before externally verified Vault escrow."""
import hashlib
import importlib.util
import json
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
import pytest

ROOT = Path(__file__).resolve().parents[1]
WORKER = ROOT / "bootstrap/dh-cloudflare-pve-haos-mtls.py"
HOST = "sample-home-pve-ha-mtls.digitalhouses.vip"


def load():
    spec = importlib.util.spec_from_file_location("haos_mtls_vault_gate", WORKER)
    obj = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obj)
    return obj


def test_certificate_writes_blocked_without_restored_git_vault(monkeypatch, tmp_path):
    m = load()
    monkeypatch.setattr(m, "MATERIAL", tmp_path)
    monkeypatch.setattr(m, "certificate_inventory", lambda *_: [])
    monkeypatch.setattr(m, "root_file", lambda p: p.read_text())
    monkeypatch.setattr(m, "guarded_binary", lambda p: p.read_bytes())
    api_calls = []
    monkeypatch.setattr(m, "api", lambda *a, **kw: api_calls.append(a))
    with pytest.raises(FileNotFoundError):
        m.certificate("mock", {"zone_id": "z"}, {"site_id": "sample-home", "hostname": HOST})
    assert api_calls == []
    assert list(tmp_path.iterdir()) == []


def test_one_home_escrow_checks_key_csr_password_and_git_pin(monkeypatch, tmp_path):
    m = load()
    monkeypatch.setattr(m, "MATERIAL", tmp_path)
    monkeypatch.setattr(m, "root_file", lambda p: p.read_text())
    monkeypatch.setattr(m, "guarded_binary", lambda p: p.read_bytes())
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(serialization.Encoding.PEM,
                            serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption())
    csr = (x509.CertificateSigningRequestBuilder()
           .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, HOST)]))
           .sign(key, hashes.SHA256()).public_bytes(serialization.Encoding.PEM))
    materials = {"client.key": pem, "client.csr": csr,
                 "p12-password": b"synthetic-escrow-password-12345678901234567890\n"}
    for name, data in materials.items():
        (tmp_path / name).write_bytes(data)
    receipt = {
        "schema_version": 1, "site_id": "sample-home",
        "hostname": HOST, "fleet_git_commit": "a" * 40,
        "fleet_vault_sha256": "b" * 64,
        "recovery_verified": True,
        "private_key_sha256": hashlib.sha256(materials["client.key"]).hexdigest(),
        "csr_sha256": hashlib.sha256(materials["client.csr"]).hexdigest(),
        "p12_password_sha256": hashlib.sha256(materials["p12-password"]).hexdigest(),
    }
    (tmp_path / "vault-escrow.json").write_text(json.dumps(receipt))
    assert m.verify_vault_escrow({"site_id": "sample-home", "hostname": HOST}) == receipt
    (tmp_path / "client.csr").write_bytes(csr + b"\n")
    with pytest.raises(m.Block, match="FLEET_VAULT_ESCROW_MATERIAL_DRIFT"):
        m.verify_vault_escrow({"site_id": "sample-home", "hostname": HOST})
    (tmp_path / "client.csr").write_bytes(csr)
    with pytest.raises(m.Block, match="FLEET_VAULT_ESCROW_RECEIPT_INVALID"):
        m.verify_vault_escrow({"site_id": "other-home", "hostname": HOST})
