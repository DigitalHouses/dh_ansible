#!/usr/bin/env python3
"""Site HA Companion PKCS#12 and private TrueNAS destination READ-ONLY audit.

Only CLI mode: --audit. Verifies issued identity restored from existing
encrypted Git Vault, round-trips password-protected PKCS#12 entirely in RAM,
then queries TrueNAS through pinned-TLS WebSocket: stat/getacl/SMB share.
No file export, no mkdir, no TrueNAS or Cloudflare mutation.
"""
from __future__ import annotations

import argparse
import ipaddress
import tempfile
import hashlib
import importlib.util
import json
from pathlib import Path
import os
import re
import socket
import ssl
import sys

from cryptography import x509
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.serialization import pkcs12
import yaml
from websockets.sync.client import connect
from ansible.parsing.vault import VaultLib, VaultSecret

ROOT = Path(__file__).resolve().parents[1]
# Bound only after a private Fleet snapshot has passed read-only validation.
PINNED_VAULT = PINNED_CERT = PINNED_GENERATION = TARGET_PARENT = None
ACL_OWNER_USERNAME = ACL_OWNER_UID = None
CERT_PATH_PARTS = ("_cert",)

class Block(Exception):
    pass


def recovery_module():
    path = ROOT / "bin/ha-mtls-vault-verify.py"
    spec = importlib.util.spec_from_file_location("mtls_p12_recovery", path)
    obj = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obj)
    return obj


def issuer_module():
    path = ROOT / "bin/ha-mtls-certificate-issue.py"
    spec = importlib.util.spec_from_file_location("mtls_p12_issuer", path)
    obj = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obj)
    return obj


def checked_site(site: str, repository: Path):
    """Validate local NAS endpoint and ACL principal from pinned private Fleet data."""
    global PINNED_VAULT, PINNED_CERT, PINNED_GENERATION, TARGET_PARENT
    global ACL_OWNER_USERNAME, ACL_OWNER_UID
    plan = issuer_module().plan_module()
    try:
        recovery = plan.accepted_issued_certificate(site, root=repository)
        state = plan.intent(site, root=repository)
    except plan.Block as exc:
        raise Block(str(exc)) from None
    if (state.get("live_writes_authorized") is not False
            or state.get("certificate_issue", {}).get("apply_authorized") is not False
            or state["waf"]["apply_authorized"] is not False):
        raise Block("FLEET_WRITES_MUST_BE_DISABLED")
    username = state["certificate_recovery"].get("acl_owner_username")
    if not isinstance(username, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{1,31}", username):
        raise Block("ACL_OWNER_IDENTITY_INVALID")
    target = Path(recovery["p12_destination"])
    if (target.name != "_cert" or target.parent.name != "digitalhouses"
            or not target.is_absolute()):
        raise Block("P12_TARGET_NOT_PINNED")
    nas_file = repository / "inventories" / site / "truenas.yml"
    if nas_file.is_symlink() or not nas_file.is_file():
        raise Block("TRUENAS_INVENTORY_NOT_PRESENT")
    nas_doc = yaml.safe_load(nas_file.read_text(encoding="utf-8"))
    truenas = nas_doc.get("truenas") if isinstance(nas_doc, dict) else None
    if not isinstance(truenas, dict):
        raise Block("TRUENAS_INVENTORY_INVALID")
    try:
        host = ipaddress.IPv4Address(truenas.get("host", ""))
    except ipaddress.AddressValueError:
        raise Block("TRUENAS_ADDRESS_INVALID") from None
    if (not plan.private_nas_ipv4(host) or truenas.get("port") != 443
            or truenas.get("endpoint") != "/api/current"
            or not re.fullmatch(r"(?:[0-9A-Fa-f]{2}:){31}[0-9A-Fa-f]{2}",
                str((truenas.get("tls") or {}).get("sha256_fingerprint", "")))):
        raise Block("TRUENAS_ENDPOINT_DRIFT")
    PINNED_VAULT = recovery["vault_sha256"]
    PINNED_CERT = recovery["certificate_sha256"]
    PINNED_GENERATION = recovery["generation_id"]
    TARGET_PARENT = str(target.parent)
    ACL_OWNER_USERNAME = username
    ACL_OWNER_UID = recovery["filebrowser_uid"]
    return state, truenas

def build_p12_in_memory(key_pem: str, cert_pem: str, password: str,
                        hostname: str):
    """PKCS#12 secret is never serialized to a local filesystem."""
    if not isinstance(password, str) or len(password) < 32:
        raise Block("P12_PASSWORD_NOT_STRONG")
    try:
        key = serialization.load_pem_private_key(key_pem.encode(), None)
        cert = x509.load_pem_x509_certificate(cert_pem.encode())
        public_der = lambda obj: obj.public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        if public_der(key.public_key()) != public_der(cert.public_key()):
            raise Block("P12_KEY_CERT_MISMATCH")
        data = pkcs12.serialize_key_and_certificates(
            name=hostname.encode("ascii"), key=key, cert=cert, cas=None,
            encryption_algorithm=serialization.BestAvailableEncryption(
                password.encode("utf-8")
            ),
        )
        new_key, new_cert, new_cas = pkcs12.load_key_and_certificates(
            data, password.encode("utf-8")
        )
        if new_key is None or new_cert is None or new_cas:
            raise Block("P12_IMPORT_FIELDS_UNEXPECTED")
        if (public_der(new_key.public_key()) != public_der(key.public_key())
                or new_cert.fingerprint(hashes.SHA256()) !=
                cert.fingerprint(hashes.SHA256())):
            raise Block("P12_ROUNDTRIP_FAILED")
        try:
            pkcs12.load_key_and_certificates(data, b"not-the-vault-password")
        except ValueError:
            pass
        else:
            raise Block("P12_UNENCRYPTED_OR_WRONG_PASSWORD_ACCEPTED")
    except (ValueError, TypeError, UnicodeError, AttributeError):
        raise Block("P12_CRYPTO_VALIDATION_FAILED") from None
    return len(data), hashlib.sha256(data).hexdigest()


def acl_rights(acl: dict) -> str:
    """Strict baseline: root-owned private dataset with one authorized site UID."""
    if (not isinstance(acl, dict) or acl.get("acltype") != "POSIX1E"
            or acl.get("uid") != 0 or acl.get("gid") != 0):
        raise Block("DIGITALHOUSES_ACL_OWNER_OR_TYPE_CHANGED")
    entries = acl.get("acl")
    if not isinstance(entries, list):
        raise Block("DIGITALHOUSES_ACL_MISSING")
    def granted(entry: dict, permission: str) -> bool:
        perms = entry.get("perms") or {}
        return perms.get(permission) is True if isinstance(perms, dict) else False
    others = [x for x in entries if x.get("tag") in ("OTHER", "GROUP_OBJ")
              and not x.get("default")]
    if len(others) != 2 or any(
        granted(x, p) for x in others for p in ("READ", "WRITE", "EXECUTE")
    ):
        raise Block("DIGITALHOUSES_ACL_GROUP_OR_OTHER_EXPOSED")
    user = [x for x in entries
            if x.get("tag") == "USER"
            and str(x.get("who")) in (ACL_OWNER_USERNAME, str(ACL_OWNER_UID))
            and not x.get("default")]
    mask = [x for x in entries if x.get("tag") == "MASK" and not x.get("default")]
    if len(user) != 1 or len(mask) != 1:
        raise Block("DIGITALHOUSES_SITE_OWNER_ACL_MISSING")
    if not all(granted(x, p) for x in (user[0], mask[0])
               for p in ("READ", "WRITE", "EXECUTE")):
        raise Block("DIGITALHOUSES_OWNER_USER_PERMISSIONS_INSUFFICIENT")
    for entry in entries:
        if entry.get("tag") == "USER" and str(entry.get("who")) not in (
            ACL_OWNER_USERNAME, str(ACL_OWNER_UID),
        ):
            raise Block("DIGITALHOUSES_OTHER_NAMED_USER_PRESENT")
    return "PRIVATE_SITE_OWNER_UID_RW"


def verify_smb_share(shares):
    if not isinstance(shares, list):
        raise Block("TRUE_NAS_SMB_LIST_INVALID")
    matching = [s for s in shares
                if s.get("name") == "digitalhouses"]
    if len(matching) != 1:
        raise Block("DIGITALHOUSES_SHARE_MISSING_OR_DUPLICATED")
    share = matching[0]
    # Emit only non-secret SMB diagnostics before failing closed.
    # Absence of a field is UNKNOWN, not implicitly safe.
    observed = {
        "SMB_OBSERVED_PATH": share.get("path", "<missing>"),
        "SMB_OBSERVED_ENABLED": share.get("enabled", "<missing>"),
        "SMB_OBSERVED_GUESTOK": share.get("guestok", "<missing>"),
        "SMB_OBSERVED_READONLY": share.get("readonly", "<missing>"),
    }
    for name, value in observed.items():
        print(name + "=" + json.dumps(value, ensure_ascii=True))
    if share.get("path") != TARGET_PARENT:
        raise Block("DIGITALHOUSES_SMB_PATH_DRIFT")
    if share.get("enabled") is not True:
        raise Block("DIGITALHOUSES_SMB_SHARE_DISABLED_OR_UNREPORTED")
    if share.get("guestok") is not False:
        raise Block("DIGITALHOUSES_SMB_GUEST_ENABLED_OR_UNREPORTED")
    return True


class ReadOnlyNas:
    def __init__(self, cfg, key):
        self.cfg, self.key = cfg, key
        self.ws, self.next_id = None, 1

    def __enter__(self):
        config = self.cfg
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        host, port = config["host"], config["port"]
        with socket.create_connection((host, port), timeout=12) as raw:
            with context.wrap_socket(raw, server_hostname=host) as tls:
                observed = hashlib.sha256(tls.getpeercert(binary_form=True)).hexdigest()
        expected = config["tls"]["sha256_fingerprint"].replace(":", "").lower()
        if observed != expected:
            raise Block("TRUENAS_TLS_FINGERPRINT_DRIFT")
        # websockets >= 17 defaults to a context-manager connector.
        # Enter explicitly to avoid deprecated implicit connection behavior.
        self._ws_manager = connect(
            f"wss://{host}:{port}{config['endpoint']}",
            ssl=context, open_timeout=12, close_timeout=12,
        )
        self.ws = self._ws_manager.__enter__()
        response = self.call("auth.login_ex", [{
            "mechanism": "API_KEY_PLAIN",
            "username": config["auth"]["username"],
            "api_key": self.key,
            "login_options": {"user_info": False},
        }])
        if not isinstance(response, dict) or response.get("response_type") != "SUCCESS":
            self._ws_manager.__exit__(None, None, None)
            raise Block("TRUENAS_AUDIT_API_AUTH_FAILED")
        return self

    def __exit__(self, exc_type, exc, tb):
        if self.ws is not None:
            self._ws_manager.__exit__(exc_type, exc, tb)

    def call(self, method, params=None, allow_missing=False):
        allowed = {
            "auth.login_ex", "filesystem.stat", "filesystem.getacl",
            "sharing.smb.query",
        }
        if method not in allowed:
            raise Block("TRUENAS_READONLY_RPC_ONLY")
        reqid = self.next_id
        self.next_id += 1
        self.ws.send(json.dumps({
            "jsonrpc": "2.0", "id": reqid,
            "method": method, "params": params or [],
        }))
        while True:
            result = json.loads(self.ws.recv(timeout=15))
            if result.get("id") != reqid:
                continue
            if result.get("error"):
                error = result["error"]
                data = error.get("data") or {}
                if allow_missing and data.get("errname") == "ENOENT":
                    return None
                raise Block("TRUENAS_RPC_" + method.replace(".", "_") + "_FAILED")
            return result.get("result")


def remote_destination_audit(client: ReadOnlyNas):
    root = client.call("filesystem.stat", [TARGET_PARENT])
    if (not isinstance(root, dict) or root.get("type") != "DIRECTORY"
            or root.get("realpath") != TARGET_PARENT):
        raise Block("DIGITALHOUSES_DATASET_PATH_DRIFT")
    acl_rights(client.call("filesystem.getacl", [TARGET_PARENT, False, True]))
    verify_smb_share(client.call("sharing.smb.query", [[], {}]))
    print("TRUE_NAS_DIGITALHOUSES_ACL=PASS")
    print("TRUE_NAS_SMB_GUEST_DISABLED=PASS")
    path = TARGET_PARENT
    missing_seen = False
    for part in CERT_PATH_PARTS:
        path += "/" + part
        node = client.call("filesystem.stat", [path], allow_missing=True)
        if node is None:
            missing_seen = True
            print("PATH_" + part.upper().replace("-", "_") + "=CREATE_LATER")
            continue
        if missing_seen:
            raise Block("TRUENAS_CHILD_EXISTS_WITH_MISSING_PARENT")
        if (node.get("type") != "DIRECTORY" or node.get("realpath") != path):
            raise Block("TRUENAS_CERTIFICATE_PATH_LINK_OR_FILE")
        print("PATH_" + part.upper().replace("-", "_") + "=EXISTS_AUDIT_REQUIRED")
    print("TARGET_WRITE_ACTION=NOT_AUTHORIZED")


def run(site):
    verifier = recovery_module()
    verifier.safe_runner(site)
    print("MODE=READ_ONLY_TRUENAS_ACL_AND_IN_MEMORY_P12")
    print("SITE=" + site)
    with tempfile.TemporaryDirectory(prefix="dh-mtls-audit-", dir=verifier.BASE) as work:
        repository, head = verifier.clone_from_github(Path(work))
        state, nas_config = checked_site(site, repository)
        ciphertext = verifier.encrypted_vault_from_clone(repository, PINNED_VAULT)
        secret = verifier.master_from_tty()
        host, key, csr, generation, entry = verifier.recovery_identity(
            ciphertext, secret, site, "issued_unpublished"
        )
        if host != state["hostname"] or generation != PINNED_GENERATION:
            raise Block("ISSUED_MTLS_IDENTITY_DRIFT")
        verifier.verify_issued_identity(host, key, csr, entry, PINNED_CERT)
        bytecount, digest = build_p12_in_memory(
            key, entry["certificate_pem"], entry["p12_password"], host
        )
        print("ISSUED_CERTIFICATE_RECOVERED=PASS")
        print("P12_ENCRYPTED_ROUNDTRIP=PASS")
        print("P12_BYTES=" + str(bytecount))
        print("P12_SHA256=" + digest)
        site_secrets = yaml.safe_load(
            VaultLib([("default", VaultSecret(secret))]).decrypt(ciphertext)
        )["fleet_secrets"]["sites"][site]
        api_key = site_secrets["truenas"]["audit_api_key"]
        if not isinstance(api_key, str) or len(api_key) < 12:
            raise Block("TRUENAS_AUDIT_CREDENTIAL_MISSING")
        with ReadOnlyNas(nas_config, api_key) as client:
            remote_destination_audit(client)
    print("TRUE_NAS_MUTATIONS=NONE")
    print("GITHUB_MUTATIONS=NONE")
    print("CLOUDFLARE_MUTATIONS=NONE")
    print("P12_SAVED_TO_DISK=NO")

if __name__ == "__main__":
    try:
        parser = argparse.ArgumentParser()
        parser.add_argument("--site", required=True)
        parser.add_argument("--audit", action="store_true", required=True)
        a = parser.parse_args()
        run(a.site)
    except Block as e:
        print("BLOCK=" + str(e), file=sys.stderr)
        sys.exit(2)
    except Exception as e:
        print("BLOCK=UNEXPECTED_" + type(e).__name__, file=sys.stderr)
        sys.exit(2)
    finally:
        print("=== DONE ===")
        print("=== TERMINAL STAYS OPEN ===")
