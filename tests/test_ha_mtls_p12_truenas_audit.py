"""Offline guards for HA Companion PKCS12 and TrueNAS read-only audit."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import importlib.util
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "bin/ha-mtls-p12-truenas-audit.py"
HOST = "sample-home-ha-mtls.digitalhouses.vip"


def mod():
    spec = importlib.util.spec_from_file_location("mtls_p12_audit", SCRIPT)
    instance = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(instance)
    instance.TARGET_PARENT = "/mnt/sample_pool/digitalhouses"
    instance.ACL_OWNER_USERNAME = "sampleowner"
    instance.ACL_OWNER_UID = 4200
    return instance


def material():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, HOST)])
    ca = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=365))
        .sign(ca, hashes.SHA256()))
    return (key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()).decode(),
        cert.public_bytes(serialization.Encoding.PEM).decode())


def private_acl():
    return {
        "acltype": "POSIX1E", "uid": 0, "gid": 0,
        "acl": [
            {"tag": "USER_OBJ", "default": False, "perms": {
                "READ": True, "WRITE": True, "EXECUTE": True}},
            {"tag": "GROUP_OBJ", "default": False, "perms": {
                "READ": False, "WRITE": False, "EXECUTE": False}},
            {"tag": "USER", "who": "sampleowner", "default": False, "perms": {
                "READ": True, "WRITE": True, "EXECUTE": True}},
            {"tag": "MASK", "default": False, "perms": {
                "READ": True, "WRITE": True, "EXECUTE": True}},
            {"tag": "OTHER", "default": False, "perms": {
                "READ": False, "WRITE": False, "EXECUTE": False}},
        ],
    }


def test_p12_roundtrip_uses_existing_strong_password_and_no_file_io(capsys, tmp_path):
    m = mod()
    key, cert = material()
    nbytes, digest = m.build_p12_in_memory(key, cert, "synthetic-strong-password-40-chars-only", HOST)
    assert 1800 <= nbytes <= 6000
    assert len(digest) == 64
    assert list(tmp_path.iterdir()) == []
    assert capsys.readouterr().out == ""


def test_p12_bad_password_and_wrong_public_key_fail():
    m = mod()
    key, cert = material()
    with pytest.raises(m.Block, match="P12_PASSWORD_NOT_STRONG"):
        m.build_p12_in_memory(key, cert, "short", HOST)
    other_key, _ = material()
    with pytest.raises(m.Block, match="P12_KEY_CERT_MISMATCH"):
        m.build_p12_in_memory(other_key, cert, "synthetic-strong-password-40-chars-only", HOST)


def test_acl_rejects_group_other_read_user_change_and_mask_loss():
    m = mod()
    good = private_acl()
    assert m.acl_rights(good) == "PRIVATE_SITE_OWNER_UID_RW"
    cases = []
    v = deepcopy(good)
    v["acl"][4]["perms"]["READ"] = True
    cases.append(v)
    v = deepcopy(good)
    v["acl"][1]["perms"]["EXECUTE"] = True
    cases.append(v)
    v = deepcopy(good)
    v["acl"][2]["who"] = "unknown"
    cases.append(v)
    v = deepcopy(good)
    v["acl"][3]["perms"]["WRITE"] = False
    cases.append(v)
    v = deepcopy(good)
    v["gid"] = 3102
    cases.append(v)
    v = deepcopy(good)
    v["acl"].append({"tag":"USER","who":"intruder","default":False,"perms":{"READ":True}})
    cases.append(v)
    for state in cases:
        with pytest.raises(m.Block):
            m.acl_rights(state)


def test_smb_must_be_private_guest_disabled_and_exact_path():
    m = mod()
    share = {"name": "digitalhouses", "path": m.TARGET_PARENT, "enabled": True,
             "guestok": False}
    assert m.verify_smb_share([share]) is True
    cases = (
        ({**share, "guestok": True}, "SMB_GUEST_ENABLED"),
        ({**share, "enabled": False}, "SMB_SHARE_DISABLED"),
        ({**share, "path": "/mnt/other"}, "SMB_PATH_DRIFT"),
        ({**share, "guestok": None}, "SMB_GUEST_ENABLED_OR_UNREPORTED"),
        ({k: v for k, v in share.items() if k != "guestok"},
         "SMB_GUEST_ENABLED_OR_UNREPORTED"),
    )
    for bad, reason in cases:
        with pytest.raises(m.Block, match=reason):
            m.verify_smb_share([bad])
    with pytest.raises(m.Block, match="MISSING_OR_DUPLICATED"):
        m.verify_smb_share([share,share])


def test_smb_diagnostics_are_nonsecret_and_available_before_block(capsys):
    m = mod()
    share = {
        "name": "digitalhouses", "path": m.TARGET_PARENT,
        "enabled": True, "guestok": True, "readonly": False,
        "password": "NEVER_LOG_THIS",
    }
    with pytest.raises(m.Block, match="SMB_GUEST_ENABLED"):
        m.verify_smb_share([share])
    output = capsys.readouterr().out
    assert "SMB_OBSERVED_PATH=" in output
    assert "SMB_OBSERVED_GUESTOK=true" in output
    assert "SMB_OBSERVED_ENABLED=true" in output
    assert "NEVER_LOG_THIS" not in output


def test_remote_audit_queries_only_expected_methods_and_does_not_write(capsys):
    m = mod()
    class Stub:
        def __init__(self):
            self.calls = []
        def call(self, method, args, allow_missing=False):
            self.calls.append((method, args))
            if method == "filesystem.stat":
                if args[0] == m.TARGET_PARENT:
                    return {"type": "DIRECTORY", "realpath": m.TARGET_PARENT}
                return None
            if method == "filesystem.getacl":
                return private_acl()
            if method == "sharing.smb.query":
                return [{"name": "digitalhouses", "path": m.TARGET_PARENT,
                         "enabled": True, "guestok": False}]
            pytest.fail("unrecognized call")
    c = Stub()
    m.remote_destination_audit(c)
    assert [n for n, _ in c.calls] == [
        "filesystem.stat","filesystem.getacl","sharing.smb.query",
        "filesystem.stat",
    ]
    out = capsys.readouterr().out
    assert "TARGET_WRITE_ACTION=NOT_AUTHORIZED" in out
    assert "CREATE_LATER" in out
    assert "PASSWORD" not in out


def test_block_existing_symlink_and_unexpected_rpc_write():
    m = mod()
    class Fake:
        def call(self, method, args, allow_missing=False):
            if method == "filesystem.stat":
                return {"type": "SYMLINK", "realpath": m.TARGET_PARENT}
            return None
    with pytest.raises(m.Block, match="DATASET_PATH_DRIFT"):
        m.remote_destination_audit(Fake())
    c = m.ReadOnlyNas({}, "synthetic")
    with pytest.raises(m.Block, match="TRUENAS_READONLY_RPC_ONLY"):
        c.call("filesystem.put", ["/mnt/sample_pool/digitalhouses/file.p12"])


def test_gates_are_disabled_and_destination_pinned(tmp_path, monkeypatch):
    m = mod()
    inventory = tmp_path / "inventories" / "sample-home"
    inventory.mkdir(parents=True)
    (inventory / "truenas.yml").write_text(
        'truenas:\n  host: ' + ".".join(("10", "23", "45", "67")) + '\n  port: 443\n  endpoint: /api/current\n'
        + '  tls:\n    sha256_fingerprint: "' + ":".join(["AA"] * 32) + '"\n',
        encoding="utf-8",
    )

    class FakePlan:
        class Block(Exception):
            pass

        @staticmethod
        def private_nas_ipv4(value):
            return str(value) == ".".join(("10", "23", "45", "67"))

        @staticmethod
        def accepted_issued_certificate(site, root):
            if site != "sample-home":
                raise FakePlan.Block("INVALID_SITE_ID")
            return {
                "p12_destination": "/mnt/sample_pool/digitalhouses/_cert",
                "vault_sha256": "a" * 64,
                "certificate_sha256": "b" * 64,
                "generation_id": "c" * 32,
                "filebrowser_uid": 4200,
            }

        @staticmethod
        def intent(site, root):
            return {
                "hostname": HOST,
                "live_writes_authorized": False,
                "certificate_issue": {"apply_authorized": False},
                "waf": {"apply_authorized": False},
                "certificate_recovery": {"acl_owner_username": "sampleowner"},
            }

    class FakeIssuer:
        @staticmethod
        def plan_module():
            return FakePlan

    monkeypatch.setattr(m, "issuer_module", lambda: FakeIssuer)
    state, truenas = m.checked_site("sample-home", tmp_path)
    assert m.TARGET_PARENT == "/mnt/sample_pool/digitalhouses"
    assert m.ACL_OWNER_USERNAME == "sampleowner"
    assert m.ACL_OWNER_UID == 4200
    assert state["live_writes_authorized"] is False
    assert truenas["host"] == ".".join(("10", "23", "45", "67"))
    with pytest.raises(m.Block, match="INVALID_SITE_ID"):
        m.checked_site("../unsafe", tmp_path)
