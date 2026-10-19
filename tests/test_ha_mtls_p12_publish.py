"""Offline safety tests for one generated P12 in existing digitalhouses/_cert."""
from __future__ import annotations
import hashlib
import importlib.util
from pathlib import Path
from datetime import datetime, timezone, timedelta

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
import pytest

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "bin/ha-mtls-p12-publish.py"


def module():
    spec = importlib.util.spec_from_file_location("mtls_p12_publish", PATH)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    # Synthetic nonproduction identity. No Fleet credentials or site inventory.
    m.VAULT_SHA = "a" * 64
    m.CERT_FINGERPRINT = "b" * 64
    m.GENERATION = "c" * 32
    m.HOST = "sample-home-ha-mtls.digitalhouses.vip"
    m.DATASET = "/mnt/sample_pool/digitalhouses"
    m.CERT_DIRECTORY = m.DATASET + "/_cert"
    m.FILE_NAME = "sample-home-ha-mtls-cccccccc.p12"
    m.TARGET = m.CERT_DIRECTORY + "/" + m.FILE_NAME
    m.PASSWORD_FILE_NAME = m.FILE_NAME[:-4] + ".password.txt"
    m.PASSWORD_TARGET = m.CERT_DIRECTORY + "/" + m.PASSWORD_FILE_NAME
    m.IPADOS_FILE_NAME = m.FILE_NAME[:-4] + "-ipados.p12"
    m.IPADOS_TARGET = m.CERT_DIRECTORY + "/" + m.IPADOS_FILE_NAME
    m.PUBLISHED_P12_SIZE = 3201
    m.FILEBROWSER_UID = 4200
    m.FILEBROWSER_GID = 4300
    return m


def fake_crypto():
    m = module()
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, m.HOST)])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
            .public_key(private.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=1))
            .not_valid_after(now + timedelta(days=365))
            .sign(private, hashes.SHA256()))
    key = private.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()
    ).decode()
    return m, key, cert


def test_p12_is_password_protected_and_kept_in_memory(monkeypatch, capsys):
    m, key, cert = fake_crypto()
    monkeypatch.setattr(m, "CERT_FINGERPRINT", cert.fingerprint(hashes.SHA256()).hex())
    entry = {
        "private_key_pem": key,
        "certificate_pem": cert.public_bytes(serialization.Encoding.PEM).decode(),
        "p12_password": "synthetic-p12-password-44-chars-minimum-only",
    }
    data = m.issue_p12(entry, m.HOST)
    assert 1800 < len(data) < 10000
    from cryptography.hazmat.primitives.serialization import pkcs12
    with pytest.raises(ValueError):
        pkcs12.load_key_and_certificates(data, b"wrong")
    assert capsys.readouterr().out == ""
    assert key.encode() not in data


class FakeNas:
    def __init__(self, missing_dir=False, existing_file=False):
        self.events = []
        self.dir = not missing_dir
        self.file = existing_file
        self.owner_dir = 4200
        self.owner_file = 0
        self.gid_file = 0
        self.file_size = 0

    def check_dir(self, path, allow_missing=False):
        self.events.append(("check_dir", path))
        assert path in ("/mnt/sample_pool/digitalhouses", "/mnt/sample_pool/digitalhouses/_cert")
        if path.endswith("/_cert") and not self.dir:
            return None
        return {"type": "DIRECTORY", "realpath": path}

    def stat(self, path, missing=False):
        self.events.append(("stat",path))
        if not self.file:
            return None
        return {"type": "FILE", "realpath": path, "size": self.file_size,
                "uid": self.owner_file, "gid": self.gid_file, "mode": 0o100600}

    def call(self, name, params):
        self.events.append((name, params))
        if name == "filesystem.mkdir":
            assert params == [{"path": "/mnt/sample_pool/digitalhouses/_cert",
                                "options": {"mode": "700", "raise_chmod_error": True}}]
            self.dir = True
            return {"type": "DIRECTORY"}
        if name == "filesystem.chown":
            obj = params[0]
            assert obj["options"] == {"recursive": False, "traverse": False}
            assert obj["uid"] == 4200 and obj["gid"] == 4300
            assert obj["path"] in (
                "/mnt/sample_pool/digitalhouses/_cert",
                "/mnt/sample_pool/digitalhouses/_cert/sample-home-ha-mtls-cccccccc.p12",
            )
            if obj["path"].endswith(".p12"):
                self.owner_file = obj["uid"]
                self.gid_file = obj["gid"]
            return 123
        pytest.fail("unexpected method "+name)

    def wait_job(self, job):
        self.events.append(("wait_job", job))
        assert job == 123

    def upload(self, target, data):
        assert target == "/mnt/sample_pool/digitalhouses/_cert/sample-home-ha-mtls-cccccccc.p12"
        self.events.append(("upload", target))
        self.file_size = len(data)
        self.file = True


def test_apply_only_creates_one_file_and_can_create_the_requested_cert_directory():
    m = module()
    payload = b"synthetic 3000-byte encrypted p12" * 100
    nas = FakeNas(missing_dir=True)
    end = m.deliver(nas, payload)
    assert m.verify_owner_file(end, len(payload))
    assert [x[0] for x in nas.events].count("filesystem.mkdir") == 1
    assert [x[0] for x in nas.events].count("upload") == 1
    assert [x[0] for x in nas.events].count("filesystem.chown") == 2


def test_existing_cert_directory_is_preserved_without_chmod_or_chown():
    m = module()
    nas = FakeNas()
    payload = b"x" * 3000
    m.deliver(nas, payload)
    assert [x[0] for x in nas.events].count("filesystem.mkdir") == 0
    assert [x[0] for x in nas.events].count("filesystem.chown") == 1


def test_existing_file_blocks_any_upload():
    m = module()
    nas = FakeNas(existing_file=True)
    with pytest.raises(m.Block, match="TARGET_P12_ALREADY_EXISTS"):
        m.deliver(nas, b"x" * 3000)
    assert not any(x[0] == "upload" for x in nas.events)


def test_stat_requires_exact_private_mode_and_filebrowser_uid_gid():
    m = module()
    val = {"type": "FILE", "realpath": m.TARGET, "size": 3000,
           "uid": 4200, "gid": 4300, "mode": 0o100600}
    assert m.verify_owner_file(val, 3000)
    for key, bad in (("realpath","/mnt/other"),("uid",0),
                     ("gid",0),("mode",0o100644),("size",0),("type","SYMLINK")):
        wrong = {**val,key:bad}
        assert not m.verify_owner_file(wrong, 3000)


def test_target_path_is_one_pinned_generation():
    m = module()
    assert m.TARGET == "/mnt/sample_pool/digitalhouses/_cert/sample-home-ha-mtls-cccccccc.p12"
    assert "filesystem.setacl" not in PATH.read_text()
    assert "cloudflare" not in "\n".join(
        line for line in PATH.read_text().splitlines()
        if '"method":' in line
    ).lower()

class PasswordFileNas:
    """Only one exact TXT file is created alongside an unchanged existing P12."""
    def __init__(self, password_exists=False, cert_ok=True):
        self.password_exists = password_exists
        self.cert_ok = cert_ok
        self.calls = []
        self.uploaded = None
        self.owner = 0
        self.gid = 0

    def check_dir(self, path, allow_missing=False):
        m = module()
        assert path in (m.DATASET, m.CERT_DIRECTORY)
        self.calls.append(("check_dir", path))
        return {"type": "DIRECTORY", "realpath": path}

    def stat(self, path, missing=False):
        m = module()
        self.calls.append(("stat", path))
        if path == m.TARGET:
            if not self.cert_ok:
                return None
            return {
                "type": "FILE", "realpath": path, "uid": 4200, "gid": 4300,
                "mode": 0o100600, "size": m.PUBLISHED_P12_SIZE,
            }
        assert path == m.PASSWORD_TARGET
        if self.uploaded is None and not self.password_exists:
            return None
        return {
            "type": "FILE", "realpath": path,
            "uid": self.owner, "gid": self.gid,
            "mode": 0o100600,
            "size": len(self.uploaded) if self.uploaded is not None else 999,
        }

    def upload(self, target, payload):
        assert target == module().PASSWORD_TARGET
        self.calls.append(("upload", target))
        self.uploaded = payload

    def call(self, name, params):
        assert name == "filesystem.chown"
        values = params[0]
        assert values["path"] == module().PASSWORD_TARGET
        assert values["options"] == {"recursive": False, "traverse": False}
        self.owner, self.gid = values["uid"], values["gid"]
        self.calls.append(("chown", name))
        return 123

    def wait_job(self, job_id):
        assert job_id == 123


def test_publish_password_txt_only_and_no_existing_certificate_mutation(capsys):
    m = module()
    nas = PasswordFileNas()
    password = "synthetic-only-super-long-import-password-123456789"
    info = m.deliver_password(nas, password)
    assert m.verify_owner_file(info, len(password.encode()) + 1, m.PASSWORD_TARGET)
    assert nas.uploaded == (password + "\n").encode("utf-8")
    assert [x[0] for x in nas.calls].count("upload") == 1
    assert [x[0] for x in nas.calls].count("chown") == 1
    assert not any(action in ("filesystem.mkdir", "filesystem.setacl") for action, _ in nas.calls)
    assert password not in capsys.readouterr().out
    assert m.PASSWORD_TARGET == (
        "/mnt/sample_pool/digitalhouses/_cert/sample-home-ha-mtls-cccccccc.password.txt"
    )


def test_duplicate_password_txt_refuses_overwrite():
    m = module()
    nas = PasswordFileNas(password_exists=True)
    with pytest.raises(m.Block, match="PASSWORD_FILE_ALREADY_EXISTS_NO_OVERWRITE"):
        m.deliver_password(nas, "synthetic-only-super-long-import-password-123456789")
    assert not any(x[0] == "upload" for x in nas.calls)


def test_no_existing_p12_refuses_password_publication():
    m = module()
    nas = PasswordFileNas(cert_ok=False)
    with pytest.raises(m.Block, match="PUBLISHED_CERT_MISSING_OR_DRIFTED"):
        m.deliver_password(nas, "synthetic-only-super-long-import-password-123456789")
    assert not any(x[0] == "upload" for x in nas.calls)


@pytest.mark.parametrize("invalid", ["short", "x" * 33 + "\n", "x" * 33 + "\x00"])
def test_invalid_password_does_not_write(invalid):
    m = module()
    nas = PasswordFileNas()
    with pytest.raises(m.Block, match="P12_IMPORT_PASSWORD_INVALID"):
        m.deliver_password(nas, invalid)
    assert not nas.calls


def test_upload_scope_allows_exactly_two_pinned_file_names():
    m = module()
    # Publisher retains an exact two-file allowlist: existing cert or companion TXT.
    script = PATH.read_text()
    assert "if filename not in (TARGET, PASSWORD_TARGET, IPADOS_TARGET):" in script
    assert "if nas.stat(PASSWORD_TARGET, missing=True) is not None:" in script
    assert 'mode": 0o600' in script
    assert "filesystem.setacl" not in script


def test_ipados_pkcs12_roundtrip_legacy_pbes_and_same_issued_identity(monkeypatch, capsys):
    import subprocess
    m, key, cert = fake_crypto()
    monkeypatch.setattr(m, "CERT_FINGERPRINT", cert.fingerprint(hashes.SHA256()).hex())
    password = "synthetic-only-47-char-import-password-for-test"
    entry = {
        "private_key_pem": key,
        "certificate_pem": cert.public_bytes(serialization.Encoding.PEM).decode(),
        "p12_password": password,
    }
    data = m.issue_ipados_p12(entry, m.HOST)
    from cryptography.hazmat.primitives.serialization import pkcs12
    restored_key, restored_cert, ca = pkcs12.load_key_and_certificates(
        data, password.encode()
    )
    assert ca is None or not ca
    assert restored_key.public_key().public_numbers() == \
           serialization.load_pem_private_key(key.encode(), None).public_key().public_numbers()
    assert restored_cert.fingerprint(hashes.SHA256()) == cert.fingerprint(hashes.SHA256())
    with pytest.raises(ValueError):
        pkcs12.load_key_and_certificates(data, b"wrong-password")
    openssl = subprocess.run(
        ["openssl", "pkcs12", "-info", "-noout",
         "-passin", "pass:" + password],
        input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    assert openssl.returncode == 0
    info = openssl.stderr.decode()
    assert "pbeWithSHA1And3-KeyTripleDES-CBC" in info
    assert "MAC: sha1" in info
    assert "AES-256-CBC" not in info
    assert password not in capsys.readouterr().out


@pytest.mark.parametrize("invalid", ["short", "x"*38+"\n", "x"*38+"\0"])
def test_ipados_p12_rejects_unsafe_password_before_serialization(invalid):
    m = module()
    with pytest.raises(m.Block, match="P12_IMPORT_PASSWORD_INVALID"):
        m.issue_ipados_p12({"p12_password": invalid}, m.HOST)


class AppleNas:
    def __init__(self, existing_ipad=False, missing_password=False):
        self.uploaded = None
        self.created = existing_ipad
        self.missing_password = missing_password
        self.owner = 0
        self.gid = 0
        self.log = []

    def check_dir(self, path, allow_missing=False):
        m = module()
        assert path in (m.DATASET, m.CERT_DIRECTORY)
        self.log.append(("check_dir", path))
        return {"realpath": path, "type": "DIRECTORY"}

    def stat(self, path, missing=False):
        m = module()
        self.log.append(("stat", path))
        if path == m.TARGET:
            return {"type": "FILE", "realpath": path, "uid": m.FILEBROWSER_UID,
                    "gid": m.FILEBROWSER_GID, "size": m.PUBLISHED_P12_SIZE,
                    "mode": 0o100600}
        if path == m.PASSWORD_TARGET:
            if self.missing_password: return None
            return {"type": "FILE", "realpath": path, "uid": m.FILEBROWSER_UID,
                    "gid": m.FILEBROWSER_GID, "size": 55, "mode": 0o100600}
        assert path == m.IPADOS_TARGET
        if not self.created:
            return None
        return {"type": "FILE", "realpath": path, "uid": self.owner,
                "gid": self.gid, "size": len(self.uploaded or b"x"*3000),
                "mode": 0o100600}

    def upload(self, path, payload):
        assert path == module().IPADOS_TARGET
        self.log.append(("upload", path))
        self.uploaded = payload
        self.created = True

    def call(self, method, params):
        m = module()
        assert method == "filesystem.chown"
        assert params == [{"path": m.IPADOS_TARGET, "uid": 4200, "gid": 4300,
                           "options": {"recursive": False, "traverse": False}}]
        self.owner, self.gid = 4200, 4300
        self.log.append(("chown", method))
        return 123

    def wait_job(self, job_id):
        assert job_id == 123


def test_ipados_p12_publishes_only_new_file_and_preserves_original():
    m = module()
    nas = AppleNas()
    payload = b"synthetic-signed-contents"*140
    out = m.deliver_ipados(nas, payload)
    assert m.verify_owner_file(out, len(payload), m.IPADOS_TARGET)
    assert [a for a,b in nas.log if a == "upload"] == ["upload"]
    assert [a for a,b in nas.log if a == "chown"] == ["chown"]
    assert all(p != m.TARGET for a,p in nas.log if a in ("upload", "chown"))
    assert m.IPADOS_TARGET == (
        "/mnt/sample_pool/digitalhouses/_cert/sample-home-ha-mtls-cccccccc-ipados.p12"
    )


def test_ipados_p12_blocks_existing_target_or_missing_original_password_file():
    m = module()
    nas = AppleNas(existing_ipad=True)
    with pytest.raises(m.Block, match="IPADOS_P12_EXISTS_NO_OVERWRITE"):
        m.deliver_ipados(nas, b"synthetic")
    assert not any(a == "upload" for a,b in nas.log)
    nas = AppleNas(missing_password=True)
    with pytest.raises(m.Block, match="EXISTING_PASSWORD_FILE_MISSING_OR_CHANGED"):
        m.deliver_ipados(nas, b"synthetic")
    assert not any(a == "upload" for a,b in nas.log)


def test_ipados_scope_in_upload_allowlist():
    script = PATH.read_text()
    assert "if filename not in (TARGET, PASSWORD_TARGET, IPADOS_TARGET):" in script
    assert "filesystem.setacl" not in script
    assert 'mode": 0o600' in script
    assert "IPADOS_COMPATIBLE_P12_PUBLISH=PASS" in script
