"""Offline fleet PKCS12 bundle contract (synthetic key, certificate, password)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import importlib.util
from pathlib import Path
import pytest
import yaml
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID

ROOT = Path(__file__).resolve().parents[1]


def module():
    path = ROOT / "bin/ha-mtls-p12-publish.py"
    spec = importlib.util.spec_from_file_location("mtls_bundle_policy", path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    m.CERT_FINGERPRINT = "b" * 64
    m.HOST = "sample-home-ha-mtls.digitalhouses.vip"
    m.DATASET = "/mnt/sample_pool/digitalhouses"
    m.CERT_DIRECTORY = m.DATASET + "/_cert"
    m.FILE_NAME = "sample-home-ha-mtls-cccccccc.p12"
    m.TARGET = m.CERT_DIRECTORY + "/" + m.FILE_NAME
    m.IPADOS_FILE_NAME = m.FILE_NAME[:-4] + "-ipados.p12"
    m.IPADOS_TARGET = m.CERT_DIRECTORY + "/" + m.IPADOS_FILE_NAME
    m.PASSWORD_FILE_NAME = m.FILE_NAME[:-4] + ".password.txt"
    m.PASSWORD_TARGET = m.CERT_DIRECTORY + "/" + m.PASSWORD_FILE_NAME
    m.PUBLISHED_P12_SIZE = 3201
    m.FILEBROWSER_UID = 3200
    m.FILEBROWSER_GID = 3102
    return m


class FakeNas:
    def __init__(self, *, existing=(), folder=True):
        self.records = {}
        self.folder = folder
        self.writes = []
        self.pending = None
        m = module()
        for name in existing:
            path = {"modern": m.TARGET,
                    "apple": m.IPADOS_TARGET,
                    "password": m.PASSWORD_TARGET}[name]
            size = (m.PUBLISHED_P12_SIZE if name == "modern" else
                    3000 if name == "apple" else 49)
            self.records[path] = dict(type="FILE", realpath=path, size=size,
                                      uid=m.FILEBROWSER_UID, gid=m.FILEBROWSER_GID,
                                      mode=0o100600)

    def check_dir(self, path, allow_missing=False):
        m = module()
        assert path in (m.DATASET, m.CERT_DIRECTORY)
        if path == m.CERT_DIRECTORY and not self.folder:
            return None
        return {"type": "DIRECTORY", "realpath": path}

    def stat(self, path, missing=False):
        return self.records.get(path)

    def call(self, method, params):
        m = module()
        self.writes.append((method, params))
        if method == "filesystem.mkdir":
            assert not self.folder
            assert params[0]["path"] == m.CERT_DIRECTORY
            self.folder = True
            return None
        assert method == "filesystem.chown"
        row = params[0]
        assert row["options"] == {"recursive": False, "traverse": False}
        assert row["uid"] == m.FILEBROWSER_UID and row["gid"] == m.FILEBROWSER_GID
        if row["path"] in self.records:
            self.records[row["path"]]["uid"] = row["uid"]
            self.records[row["path"]]["gid"] = row["gid"]
        else:
            assert row["path"] == m.CERT_DIRECTORY
        return 79

    def wait_job(self, job_id):
        assert job_id == 79

    def upload(self, path, payload):
        m = module()
        assert path in (m.TARGET, m.IPADOS_TARGET, m.PASSWORD_TARGET)
        assert path not in self.records
        self.writes.append(("upload", path))
        self.records[path] = dict(type="FILE", realpath=path, size=len(payload),
                                  uid=0, gid=0, mode=0o100600)


def fake_identity(m, monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, m.HOST)])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(days=1))
            .not_valid_after(now + timedelta(days=366))
            .sign(key, hashes.SHA256()))
    monkeypatch.setattr(m, "CERT_FINGERPRINT", cert.fingerprint(hashes.SHA256()).hex())
    return {"private_key_pem": key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()).decode(),
        "certificate_pem": cert.public_bytes(serialization.Encoding.PEM).decode(),
        "p12_password": "SYNTHETIC-VERY-LONG-TEST-ONLY-PASSWORD-45",
    }


def test_bundle_declaration_matches_synthetic_site_metadata():
    m = module()
    assert m.HOST == "sample-home-ha-mtls.digitalhouses.vip"
    assert m.CERT_DIRECTORY == "/mnt/sample_pool/digitalhouses/_cert"
    assert m.FILE_NAME == "sample-home-ha-mtls-cccccccc.p12"
    assert m.IPADOS_FILE_NAME == "sample-home-ha-mtls-cccccccc-ipados.p12"
    assert m.PASSWORD_FILE_NAME == "sample-home-ha-mtls-cccccccc.password.txt"
    assert m.FILEBROWSER_UID == 3200
    assert m.FILEBROWSER_GID == 3102
    assert m.PUBLISHED_P12_SIZE == 3201



def test_converged_bundle_is_idempotent_zero_writes(capsys):
    m = module()
    nas = FakeNas(existing=("modern", "apple", "password"))
    result = m.publish_bundle(nas, {}, m.HOST)
    assert set(result.values()) == {"NOOP"}
    assert nas.writes == []
    captured = capsys.readouterr().out
    assert "BUNDLE_PUBLISH=PASS" in captured
    assert "BUNDLE_MODERN_P12=NOOP" in captured
    assert "BUNDLE_IPADOS_P12=NOOP" in captured
    assert "BUNDLE_PASSWORD_TXT=NOOP" in captured


def test_new_site_bundle_creates_only_private_folder_and_three_files(monkeypatch, capsys):
    m = module()
    entry = fake_identity(m, monkeypatch)
    nas = FakeNas(folder=False)
    result = m.publish_bundle(nas, entry, m.HOST)
    assert set(result.values()) == {"CREATE"}
    uploads = [path for method,path in nas.writes if method == "upload"]
    assert uploads == [m.TARGET, m.IPADOS_TARGET, m.PASSWORD_TARGET]
    assert set(nas.records) == set(uploads)
    for target in uploads:
        observed = nas.stat(target)
        assert observed["uid"] == 3200 and observed["gid"] == 3102
        assert observed["mode"] & 0o777 == 0o600
    assert [method for method,_ in nas.writes].count("filesystem.mkdir") == 1
    assert len([x for x in nas.writes if x[0] == "filesystem.chown"]) == 4
    assert "BUNDLE_PUBLISH=PASS" in capsys.readouterr().out


def test_partial_bundle_only_fills_missing_password_file_no_p12_overwrite(capsys):
    m = module()
    nas = FakeNas(existing=("modern", "apple"))
    pw = "synthetic-very-long-secret-sample-verified-123"
    result = m.publish_bundle(nas, {"p12_password": pw}, m.HOST)
    assert result[m.TARGET] == "NOOP"
    assert result[m.IPADOS_TARGET] == "NOOP"
    assert result[m.PASSWORD_TARGET] == "CREATE"
    assert [target for method,target in nas.writes if method == "upload"] == [m.PASSWORD_TARGET]
    assert pw not in capsys.readouterr().out


def test_existing_invalid_mode_blocks_before_any_mutation():
    m = module()
    nas = FakeNas(existing=("modern", "apple"))
    nas.records[m.TARGET]["mode"] = 0o100644
    with pytest.raises(m.Block, match="BUNDLE_EXISTING_FILE_METADATA_DRIFT"):
        m.publish_bundle(nas, {}, m.HOST)
    assert nas.writes == []


def test_complete_bundle_does_not_require_plaintext_secrets_to_be_logged():
    m = module()
    data = (ROOT / "bin/ha-mtls-p12-publish.py").read_text()
    assert 'mode.add_argument("--publish-standard-bundle"' in data
    assert "print(pwd" not in data
    assert "print(entry[" not in data
    assert "if nas.stat(path, missing=True) is not None:" in data
