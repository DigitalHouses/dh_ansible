#!/usr/bin/env python3
"""Publish one existing site client PKCS#12 to TrueNAS digitalhouses/_cert.

Single narrow write: create _cert if missing, upload new generation P12,
set ONLY its owner to the already-running FileBrowser UID from private site inventory. Uses the
already-encrypted Fleet Vault and TrueNAS pinned HTTPS/JSON-RPC API.
No Cloudflare, WAF, DNS, app config, ACL or other dataset changes.
"""
from __future__ import annotations

import argparse
import ipaddress
import tempfile
from datetime import datetime
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import socket
import ssl
import subprocess
import sys
import time

from ansible.parsing.vault import VaultLib, VaultSecret
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.serialization import pkcs12
import urllib3
from websockets.sync.client import connect
import yaml

ROOT = Path(__file__).resolve().parents[1]
# Initialized only after a pinned private Fleet clone passes all safety checks.
VAULT_SHA = CERT_FINGERPRINT = GENERATION = HOST = None
DATASET = CERT_DIRECTORY = FILE_NAME = TARGET = None
PASSWORD_FILE_NAME = PASSWORD_TARGET = IPADOS_FILE_NAME = IPADOS_TARGET = None
PUBLISHED_P12_SIZE = FILEBROWSER_UID = FILEBROWSER_GID = None

class Block(Exception):
    pass


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "bin" / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def checked_state(site, repository):
    """Bind publish scope to a fresh, encrypted private Fleet snapshot."""
    global VAULT_SHA, CERT_FINGERPRINT, GENERATION, HOST
    global DATASET, CERT_DIRECTORY, FILE_NAME, TARGET
    global PASSWORD_FILE_NAME, PASSWORD_TARGET, IPADOS_FILE_NAME, IPADOS_TARGET
    global PUBLISHED_P12_SIZE, FILEBROWSER_UID, FILEBROWSER_GID
    verifier = load_module("ha-mtls-vault-verify.py", "mtls_cert_recovery")
    verifier.safe_runner(site)
    plan = load_module("ha-mtls-certificate-plan.py", "mtls_site_policy")
    try:
        recovery = plan.accepted_issued_certificate(site, root=repository)
        cf_state = plan.intent(site, root=repository)
    except plan.Block as exc:
        raise Block(str(exc)) from None
    destination = Path(recovery["p12_destination"])
    if (destination.name != "_cert" or destination.parent.name != "digitalhouses"
            or destination.parent.parent == destination.parent):
        raise Block("P12_DESTINATION_SCOPE_INVALID")
    bundle = cf_state["p12_bundle"]
    expected_bundle = {
        "layout": "dual-pkcs12-with-password-sidecar",
        "formats": ["modern", "apple-compatible"],
        "password_sidecar": True,
        "output_directory_mode": "0700",
        "output_file_mode": "0600",
        "filebrowser_uid": recovery["filebrowser_uid"],
        "filebrowser_gid": recovery["filebrowser_gid"],
        "filename_scheme": "host-prefix-and-generation8",
        "publish_policy": "no-overwrite-idempotent",
        "vault_password_source": "existing-encrypted-fleet-vault",
    }
    if bundle != expected_bundle:
        raise Block("FLEET_P12_PUBLICATION_POLICY_DRIFT")
    if (cf_state.get("live_writes_authorized") is not False
            or cf_state.get("certificate_issue", {}).get("apply_authorized") is not False
            or cf_state["waf"]["apply_authorized"] is not False):
        raise Block("MTLS_WRITES_OR_HOST_DRIFT")
    inventory = repository / "inventories" / site
    truenas_file = inventory / "truenas.yml"
    apps_file = inventory / "truenas_apps.yml"
    if (truenas_file.is_symlink() or apps_file.is_symlink()
            or not truenas_file.is_file() or not apps_file.is_file()):
        raise Block("TRUENAS_INVENTORY_NOT_PRESENT")
    nas_doc = yaml.safe_load(truenas_file.read_text(encoding="utf-8"))
    nas = nas_doc.get("truenas") if isinstance(nas_doc, dict) else None
    if not isinstance(nas, dict):
        raise Block("TRUENAS_INVENTORY_INVALID")
    try:
        address = ipaddress.IPv4Address(nas.get("host", ""))
    except ipaddress.AddressValueError:
        raise Block("TRUENAS_ADDRESS_INVALID") from None
    if (not plan.private_nas_ipv4(address) or nas.get("port") != 443
            or nas.get("endpoint") != "/api/current"
            or not re.fullmatch(r"(?:[0-9A-Fa-f]{2}:){31}[0-9A-Fa-f]{2}",
                                str((nas.get("tls") or {}).get("sha256_fingerprint", "")))
            or not isinstance((nas.get("auth") or {}).get("write_username"), str)
            or not nas["auth"]["write_username"]):
        raise Block("TRUENAS_ENDPOINT_DRIFT")
    apps_doc = yaml.safe_load(apps_file.read_text(encoding="utf-8"))
    apps = apps_doc.get("truenas_apps") if isinstance(apps_doc, dict) else None
    entries = apps.get("applications") if isinstance(apps, dict) else None
    if not isinstance(entries, list):
        raise Block("TRUENAS_APPS_INVALID")
    matches = [x for x in entries if isinstance(x, dict)
               and x.get("name") == "filebrowser-quantum"]
    if len(matches) != 1:
        raise Block("FILEBROWSER_INVENTORY_DRIFT")
    values = matches[0].get("values")
    if not isinstance(values, dict):
        raise Block("FILEBROWSER_VALUES_INVALID")
    storage = (values.get("storage") or {}).get("additional_storage")
    if (values.get("run_as") != {"user": recovery["filebrowser_uid"],
                                 "group": recovery["filebrowser_gid"]}
            or not isinstance(storage, list)
            or not any(isinstance(x, dict)
                       and x.get("mount_path") == "/data/digitalhouses"
                       and (x.get("host_path_config") or {}).get("path") == str(destination.parent)
                       and x.get("read_only") is False for x in storage)):
        raise Block("FILEBROWSER_RW_MOUNT_DRIFT")
    # The site is now fully pinned; no mutation has occurred.
    VAULT_SHA = recovery["vault_sha256"]
    CERT_FINGERPRINT = recovery["certificate_sha256"]
    GENERATION = recovery["generation_id"]
    HOST = recovery["hostname"]
    CERT_DIRECTORY = str(destination)
    DATASET = str(destination.parent)
    FILE_NAME = recovery["p12_filename"]
    TARGET = CERT_DIRECTORY + "/" + FILE_NAME
    PASSWORD_FILE_NAME = FILE_NAME[:-4] + ".password.txt"
    PASSWORD_TARGET = CERT_DIRECTORY + "/" + PASSWORD_FILE_NAME
    IPADOS_FILE_NAME = FILE_NAME[:-4] + "-ipados.p12"
    IPADOS_TARGET = CERT_DIRECTORY + "/" + IPADOS_FILE_NAME
    PUBLISHED_P12_SIZE = recovery["published_p12_size_bytes"]
    FILEBROWSER_UID = recovery["filebrowser_uid"]
    FILEBROWSER_GID = recovery["filebrowser_gid"]
    return verifier, nas

def issue_p12(entry, hostname):
    password = entry["p12_password"]
    if not isinstance(password, str) or len(password) < 32:
        raise Block("P12_IMPORT_PASSWORD_INVALID")
    private = serialization.load_pem_private_key(entry["private_key_pem"].encode(), None)
    cert = x509.load_pem_x509_certificate(entry["certificate_pem"].encode())
    p12 = pkcs12.serialize_key_and_certificates(
        name=hostname.encode("ascii"), key=private, cert=cert, cas=None,
        encryption_algorithm=serialization.BestAvailableEncryption(password.encode()),
    )
    recovered, recovered_cert, cas = pkcs12.load_key_and_certificates(p12, password.encode())
    if (not recovered or not recovered_cert or cas
            or recovered_cert.fingerprint(hashes.SHA256()).hex() != CERT_FINGERPRINT
            or recovered.public_key().public_numbers() != private.public_key().public_numbers()):
        raise Block("PKCS12_ROUNDTRIP_FAILED")
    if len(p12) < 1800 or len(p12) > 10000:
        raise Block("PKCS12_SIZE_OUT_OF_RANGE")
    return p12


def issue_ipados_p12(entry, hostname):
    """Same issued identity + existing password, legacy Apple PKCS12 envelope.

    Uses PBESv1/3DES and SHA1 only for old Apple import compatibility.
    This is weaker *container encryption*, not a certificate/key rotation.
    The password is a separate existing high-entropy Fleet Vault value.
    """
    password = entry.get("p12_password")
    if (not isinstance(password, str) or len(password) < 32
            or any(char in password for char in ("\r", "\n", "\x00"))):
        raise Block("P12_IMPORT_PASSWORD_INVALID")
    key = serialization.load_pem_private_key(
        entry["private_key_pem"].encode("ascii"), password=None
    )
    cert = x509.load_pem_x509_certificate(
        entry["certificate_pem"].encode("ascii")
    )
    encryption = (
        serialization.PrivateFormat.PKCS12.encryption_builder()
        .kdf_rounds(50000)
        .key_cert_algorithm(pkcs12.PBES.PBESv1SHA1And3KeyTripleDESCBC)
        .hmac_hash(hashes.SHA1())
        .build(password.encode("utf-8"))
    )
    data = pkcs12.serialize_key_and_certificates(
        name=hostname.encode("ascii"), key=key, cert=cert,
        cas=None, encryption_algorithm=encryption,
    )
    recovered_key, recovered_cert, other = (
        pkcs12.load_key_and_certificates(data, password.encode("utf-8"))
    )
    if (recovered_key is None or recovered_cert is None or other
            or recovered_cert.fingerprint(hashes.SHA256()).hex() != CERT_FINGERPRINT
            or recovered_key.public_key().public_numbers() !=
            key.public_key().public_numbers()):
        raise Block("IPADOS_P12_CERTIFICATE_OR_KEY_ROUNDTRIP_FAILED")
    try:
        pkcs12.load_key_and_certificates(data, b"incorrect-test-password")
    except ValueError:
        pass
    else:
        raise Block("IPADOS_P12_PASSWORD_NOT_ENFORCED")
    if not 1800 <= len(data) <= 10000:
        raise Block("IPADOS_P12_SIZE_OUT_OF_RANGE")
    return data


def peer_fingerprint(host, port):
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    with socket.create_connection((host, port), timeout=12) as sock:
        with ctx.wrap_socket(sock, server_hostname=host) as tls:
            return hashlib.sha256(tls.getpeercert(binary_form=True)).hexdigest()


class Nas:
    """Only the four named write/read methods needed for this single file."""
    ALLOWED = {
        "auth.login_ex", "filesystem.stat", "filesystem.mkdir",
        "filesystem.chown", "core.get_jobs",
    }

    def __init__(self, config, key):
        self.config, self.key = config, key
        self.manager = self.ws = None
        self.seq = 0

    def __enter__(self):
        c = self.config
        observed = peer_fingerprint(c["host"], c["port"])
        expected = c["tls"]["sha256_fingerprint"].replace(":", "").lower()
        if observed != expected:
            raise Block("TRUENAS_TLS_PIN_MISMATCH")
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        self.manager = connect(
            f"wss://{c['host']}:{c['port']}{c['endpoint']}",
            ssl=ctx, open_timeout=15, close_timeout=15,
        )
        self.ws = self.manager.__enter__()
        try:
            auth = self.call("auth.login_ex", [{
                "mechanism": "API_KEY_PLAIN",
                "username": c["auth"]["write_username"],
                "api_key": self.key,
                "login_options": {"user_info": False},
            }])
            if not isinstance(auth, dict) or auth.get("response_type") != "SUCCESS":
                raise Block("TRUENAS_WRITE_API_AUTH_DENIED")
        except Exception:
            self.manager.__exit__(None, None, None)
            raise
        return self

    def __exit__(self, typ, exc, traceback):
        if self.manager:
            self.manager.__exit__(typ, exc, traceback)

    def call(self, method, params=None, missing=False):
        if method not in self.ALLOWED:
            raise Block("UNEXPECTED_TRUENAS_MUTATION")
        self.seq += 1
        i = self.seq
        self.ws.send(json.dumps({
            "jsonrpc": "2.0", "method": method, "id": i,
            "params": params or [],
        }))
        while True:
            result = json.loads(self.ws.recv(timeout=30))
            if result.get("id") != i:
                continue
            if result.get("error"):
                data = result["error"].get("data") or {}
                if missing and data.get("errname") == "ENOENT":
                    return None
                raise Block("TRUENAS_" + method.replace(".", "_").upper() + "_REJECTED")
            return result.get("result")

    def stat(self, path, missing=False):
        return self.call("filesystem.stat", [path], missing=missing)

    def check_dir(self, path, allow_missing=False):
        item = self.stat(path, missing=allow_missing)
        if item is not None and (
            item.get("type") != "DIRECTORY" or item.get("realpath") != path
        ):
            raise Block("TARGET_DIRECTORY_SYMLINK_OR_DRIFT")
        return item

    def wait_job(self, job_id):
        if not isinstance(job_id, int) or isinstance(job_id, bool):
            raise Block("TRUENAS_JOB_ID_INVALID")
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            jobs = self.call("core.get_jobs", [[[ "id", "=", job_id ]], {}])
            if isinstance(jobs, list) and len(jobs) == 1:
                state = jobs[0].get("state")
                if state == "SUCCESS":
                    return
                if state in ("FAILED", "ABORTED"):
                    raise Block("TRUENAS_FILE_JOB_FAILED")
            time.sleep(1)
        raise Block("TRUENAS_FILE_JOB_TIMEOUT")

    def upload(self, filename, content):
        """TrueNAS job upload over fingerprint-pinned HTTPS (in-memory bytes)."""
        if filename not in (TARGET, PASSWORD_TARGET, IPADOS_TARGET):
            raise Block("TARGET_OUTSIDE_APPROVED_CERT_FOLDER")
        basename = {TARGET: FILE_NAME, PASSWORD_TARGET: PASSWORD_FILE_NAME,
                    IPADOS_TARGET: IPADOS_FILE_NAME}[filename]
        content_type = ("text/plain; charset=utf-8" if filename == PASSWORD_TARGET
                        else "application/x-pkcs12")
        api_host = self.config["host"]
        expected = self.config["tls"]["sha256_fingerprint"].replace(":", "")
        pool = urllib3.PoolManager(
            cert_reqs="CERT_NONE", assert_fingerprint=expected,
            retries=False, timeout=urllib3.Timeout(connect=10, read=45),
        )
        fields = [
            ("data", json.dumps({
                "method": "filesystem.put",
                "params": [filename, {"append": False, "mode": 0o600}],
            })),
            ("file", (basename, content, content_type)),
        ]
        try:
            # Disable env proxies by using urllib3 PoolManager directly.
            response = pool.request(
                "POST", f"https://{api_host}:{self.config['port']}/_upload/",
                fields=fields, encode_multipart=True,
                headers={"Authorization": "Bearer " + self.key},
                redirect=False,
            )
        except Exception:
            # POST outcome may be ambiguous; never retry without stat.
            raise Block("TRUENAS_UPLOAD_OUTCOME_UNKNOWN_RECHECK_TARGET") from None
        if response.status != 200:
            raise Block("TRUENAS_UPLOAD_HTTP_" + str(response.status))
        try:
            job = json.loads(response.data)
            jobid = job.get("job_id")
        except (ValueError, TypeError, AttributeError):
            raise Block("TRUENAS_UPLOAD_JOB_RESPONSE_INVALID") from None
        self.wait_job(jobid)


def verify_owner_file(stat, expected_size, target=None):
    if target is None:
        target = TARGET
    return (isinstance(stat, dict)
            and stat.get("type") == "FILE" and stat.get("realpath") == target
            and stat.get("size") == expected_size
            and stat.get("uid") == FILEBROWSER_UID
            and stat.get("gid") == FILEBROWSER_GID
            and (stat.get("mode", 0) & 0o777) == 0o600)


def deliver(nas, payload):
    nas.check_dir(DATASET)
    folder = nas.check_dir(CERT_DIRECTORY, allow_missing=True)
    if folder is None:
        # Only requested child _cert; never create/change a dataset or ACL.
        nas.call("filesystem.mkdir", [{
            "path": CERT_DIRECTORY,
            "options": {"mode": "700", "raise_chmod_error": True},
        }])
        nas.wait_job(nas.call("filesystem.chown", [{
            "path": CERT_DIRECTORY,
            "uid": FILEBROWSER_UID,
            "gid": FILEBROWSER_GID,
            "options": {"recursive": False, "traverse": False},
        }]))
        nas.check_dir(CERT_DIRECTORY)
        print("TARGET_CERT_DIRECTORY=CREATED")
    else:
        print("TARGET_CERT_DIRECTORY=EXISTS")
    current = nas.stat(TARGET, missing=True)
    if current is not None:
        # No destructive overwrite, even after an ambiguous previous upload.
        raise Block("TARGET_P12_ALREADY_EXISTS_REVIEW_BEFORE_RETRY")
    print("TARGET_FILE_BEFORE_UPLOAD=ABSENT")
    nas.upload(TARGET, payload)
    after = nas.stat(TARGET)
    if (after.get("type") != "FILE" or after.get("realpath") != TARGET
            or after.get("size") != len(payload)):
        raise Block("UPLOADED_P12_FILE_STAT_MISMATCH")
    nas.wait_job(nas.call("filesystem.chown", [{
        "path": TARGET, "uid": FILEBROWSER_UID, "gid": FILEBROWSER_GID,
        "options": {"recursive": False, "traverse": False},
    }]))
    final = nas.stat(TARGET)
    if not verify_owner_file(final, len(payload)):
        raise Block("P12_FILE_OWNER_MODE_OR_SIZE_MISMATCH")
    return final


def deliver_password(nas, import_password):
    """Write only companion plaintext file; never modify the existing PKCS#12."""
    if (not isinstance(import_password, str) or len(import_password) < 32
            or any(ch in import_password for ch in ("\r", "\n", "\x00"))):
        raise Block("P12_IMPORT_PASSWORD_INVALID")
    nas.check_dir(DATASET)
    if nas.check_dir(CERT_DIRECTORY, allow_missing=True) is None:
        raise Block("EXISTING_CERT_DIRECTORY_MISSING")
    existing_cert = nas.stat(TARGET, missing=True)
    if not verify_owner_file(existing_cert, PUBLISHED_P12_SIZE):
        raise Block("PUBLISHED_CERT_MISSING_OR_DRIFTED")
    if nas.stat(PASSWORD_TARGET, missing=True) is not None:
        raise Block("PASSWORD_FILE_ALREADY_EXISTS_NO_OVERWRITE")
    payload = (import_password + "\n").encode("utf-8")
    nas.upload(PASSWORD_TARGET, payload)
    result = nas.stat(PASSWORD_TARGET)
    if (not isinstance(result, dict) or result.get("type") != "FILE"
            or result.get("realpath") != PASSWORD_TARGET
            or result.get("size") != len(payload)):
        raise Block("PASSWORD_FILE_STAT_MISMATCH")
    nas.wait_job(nas.call("filesystem.chown", [{
        "path": PASSWORD_TARGET, "uid": FILEBROWSER_UID, "gid": FILEBROWSER_GID,
        "options": {"recursive": False, "traverse": False},
    }]))
    final = nas.stat(PASSWORD_TARGET)
    if not verify_owner_file(final, len(payload), PASSWORD_TARGET):
        raise Block("PASSWORD_FILE_PERMISSIONS_MISMATCH")
    return final


def deliver_ipados(nas, p12):
    """Publish only new iPadOS-compatible P12; preserve both original files."""
    nas.check_dir(DATASET)
    if nas.check_dir(CERT_DIRECTORY, allow_missing=True) is None:
        raise Block("EXISTING_CERT_DIRECTORY_MISSING")
    original = nas.stat(TARGET, missing=True)
    if not verify_owner_file(original, PUBLISHED_P12_SIZE):
        raise Block("ORIGINAL_P12_MISSING_OR_CHANGED")
    password_file = nas.stat(PASSWORD_TARGET, missing=True)
    if (not isinstance(password_file, dict)
            or not 33 <= password_file.get("size", -1) <= 512
            or not verify_owner_file(password_file, password_file["size"], PASSWORD_TARGET)):
        raise Block("EXISTING_PASSWORD_FILE_MISSING_OR_CHANGED")
    if nas.stat(IPADOS_TARGET, missing=True) is not None:
        raise Block("IPADOS_P12_EXISTS_NO_OVERWRITE")
    nas.upload(IPADOS_TARGET, p12)
    created = nas.stat(IPADOS_TARGET)
    if (not isinstance(created, dict) or created.get("type") != "FILE"
            or created.get("realpath") != IPADOS_TARGET
            or created.get("size") != len(p12)):
        raise Block("IPADOS_P12_STAT_MISMATCH")
    nas.wait_job(nas.call("filesystem.chown", [{
        "path": IPADOS_TARGET,
        "uid": FILEBROWSER_UID,
        "gid": FILEBROWSER_GID,
        "options": {"recursive": False, "traverse": False},
    }]))
    after = nas.stat(IPADOS_TARGET)
    if not verify_owner_file(after, len(p12), IPADOS_TARGET):
        raise Block("IPADOS_P12_OWNER_MODE_OR_SIZE_MISMATCH")
    if not verify_owner_file(nas.stat(TARGET), PUBLISHED_P12_SIZE):
        raise Block("ORIGINAL_P12_DRIFTED_AFTER_WRITE")
    return after


def verify_bundle_file(path, stat):
    """Metadata gate for a previously published file; never overwrite on drift."""
    if path not in (TARGET, IPADOS_TARGET, PASSWORD_TARGET):
        raise Block("BUNDLE_FILE_OUTSIDE_ALLOWED_NAMES")
    if not isinstance(stat, dict):
        return False
    size = stat.get("size")
    lo, hi = ((33, 512) if path == PASSWORD_TARGET else (1800, 10000))
    return (isinstance(size, int) and not isinstance(size, bool)
            and lo <= size <= hi
            and verify_owner_file(stat, size, target=path))


def publish_bundle(nas, entry, hostname):
    """Reconcile three owner-private files; publish missing ones, no replacements.

    Issuance/rotation must first commit and independently recover the
    issued cert + password from encrypted Fleet Vault. Publication is
    deliberately a separate commit-safe step, not a Cloudflare API side effect.
    """
    nas.check_dir(DATASET)
    folder = nas.check_dir(CERT_DIRECTORY, allow_missing=True)
    states = {}
    for path in (TARGET, IPADOS_TARGET, PASSWORD_TARGET):
        observed = nas.stat(path, missing=True)
        if observed is not None and not verify_bundle_file(path, observed):
            raise Block("BUNDLE_EXISTING_FILE_METADATA_DRIFT")
        states[path] = "NOOP" if observed is not None else "CREATE"
    if folder is None:
        if any(value == "NOOP" for value in states.values()):
            raise Block("BUNDLE_CHILD_EXISTS_WITH_MISSING_PARENT")
        nas.call("filesystem.mkdir", [{
            "path": CERT_DIRECTORY,
            "options": {"mode": "700", "raise_chmod_error": True},
        }])
        nas.wait_job(nas.call("filesystem.chown", [{
            "path": CERT_DIRECTORY,
            "uid": FILEBROWSER_UID, "gid": FILEBROWSER_GID,
            "options": {"recursive": False, "traverse": False},
        }]))
        nas.check_dir(CERT_DIRECTORY)
        print("BUNDLE_PRIVATE_DIRECTORY=CREATED")
    else:
        print("BUNDLE_PRIVATE_DIRECTORY=EXISTS")

    def add(path, payload):
        if nas.stat(path, missing=True) is not None:
            raise Block("BUNDLE_FILE_APPEARED_DURING_APPLY")
        nas.upload(path, payload)
        stat = nas.stat(path)
        if (not isinstance(stat, dict) or stat.get("realpath") != path
                or stat.get("type") != "FILE" or stat.get("size") != len(payload)):
            raise Block("BUNDLE_NEW_FILE_SIZE_OR_PATH_DRIFT")
        nas.wait_job(nas.call("filesystem.chown", [{
            "path": path, "uid": FILEBROWSER_UID, "gid": FILEBROWSER_GID,
            "options": {"recursive": False, "traverse": False},
        }]))
        if not verify_owner_file(nas.stat(path), len(payload), path):
            raise Block("BUNDLE_NEW_FILE_OWNER_OR_MODE_DRIFT")

    if states[TARGET] == "CREATE":
        add(TARGET, issue_p12(entry, hostname))
    if states[IPADOS_TARGET] == "CREATE":
        add(IPADOS_TARGET, issue_ipados_p12(entry, hostname))
    if states[PASSWORD_TARGET] == "CREATE":
        pwd = entry.get("p12_password")
        if (not isinstance(pwd, str) or len(pwd) < 32
                or any(ch in pwd for ch in ("\r", "\n", "\x00"))):
            raise Block("P12_IMPORT_PASSWORD_INVALID")
        add(PASSWORD_TARGET, (pwd + "\n").encode("utf-8"))

    for path in (TARGET, IPADOS_TARGET, PASSWORD_TARGET):
        if not verify_bundle_file(path, nas.stat(path)):
            raise Block("BUNDLE_FINAL_METADATA_VERIFICATION_FAILED")
    print("BUNDLE_MODERN_P12=" + states[TARGET])
    print("BUNDLE_IPADOS_P12=" + states[IPADOS_TARGET])
    print("BUNDLE_PASSWORD_TXT=" + states[PASSWORD_TARGET])
    print("BUNDLE_EXISTING_FILES_OVERWRITTEN=NO")
    print("BUNDLE_FILES_MODE=0600")
    print("BUNDLE_FILES_OWNER=" + str(FILEBROWSER_UID) + ":" + str(FILEBROWSER_GID))
    print("BUNDLE_PUBLISH=PASS")
    return states


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--site", required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--publish-to-cert", action="store_true")
    mode.add_argument("--publish-password-file", action="store_true")
    mode.add_argument("--publish-ipados-compatible", action="store_true")
    mode.add_argument("--publish-standard-bundle", action="store_true")
    options = parser.parse_args()
    verifier = load_module("ha-mtls-vault-verify.py", "mtls_cert_recovery")
    verifier.safe_runner(options.site)
    with tempfile.TemporaryDirectory(prefix="dh-mtls-publish-", dir=verifier.BASE) as work:
        repository, _ = verifier.clone_from_github(Path(work))
        verifier, nas_config = checked_state(options.site, repository)
        encrypted = verifier.encrypted_vault_from_clone(repository, VAULT_SHA)
        password = verifier.master_from_tty()
        hostname, key, csr, generation, entry = verifier.recovery_identity(
            encrypted, password, options.site, "issued_unpublished"
        )
        if hostname != HOST or generation != GENERATION:
            raise Block("CLIENT_CERTIFICATE_GENERATION_DRIFT")
        verifier.verify_issued_identity(hostname, key, csr, entry, CERT_FINGERPRINT)
        if options.publish_to_cert:
            p12_data = issue_p12(entry, hostname)
        elif options.publish_ipados_compatible:
            p12_data = issue_ipados_p12(entry, hostname)
        else:
            p12_data = None
        values = yaml.safe_load(
            VaultLib([("default", VaultSecret(password))]).decrypt(encrypted)
        )["fleet_secrets"]["sites"][options.site]
        write_key = values["truenas"]["write_api_key"]
        if not isinstance(write_key, str) or len(write_key) < 12:
            raise Block("EXISTING_TRUENAS_WRITE_API_KEY_MISSING")
        mode_title = ("STANDARD_DUAL_P12_AND_PASSWORD_TXT" if options.publish_standard_bundle
                      else "ONE_P12_TO_DIGITALHOUSES_CERT_ONLY" if options.publish_to_cert
                      else "ONE_IPADOS_COMPATIBLE_P12_NO_REISSUE" if options.publish_ipados_compatible
                      else "ONE_IMPORT_PASSWORD_TXT_ALONGSIDE_EXISTING_P12")
        print("MODE=" + mode_title)
        print("ISSUED_CERTIFICATE_RECOVERY=PASS")
        with Nas(nas_config, write_key) as client:
            if options.publish_standard_bundle:
                bundle_state = publish_bundle(client, entry, hostname)
                info = None
            elif options.publish_to_cert:
                info = deliver(client, p12_data)
            elif options.publish_ipados_compatible:
                info = deliver_ipados(client, p12_data)
            else:
                info = deliver_password(client, entry["p12_password"])
        if options.publish_standard_bundle:
            print("STANDARD_BUNDLE_DIRECTORY=" + CERT_DIRECTORY)
            print("ORIGINAL_CERTIFICATE_REISSUED=NO")
            print("IMPORT_PASSWORD_REUSED_FROM_FLEET_VAULT=YES")
        elif options.publish_to_cert:
            print("PKCS12_ENCRYPTED_ROUNDTRIP=PASS")
            print("P12_PUBLISH=PASS")
            print("P12_PATH=" + TARGET)
            print("P12_SIZE_BYTES=" + str(len(p12_data)))
            print("P12_SHA256=" + hashlib.sha256(p12_data).hexdigest())
            print("IMPORT_PASSWORD=ENCRYPTED_FLEET_VAULT_ONLY")
        elif options.publish_ipados_compatible:
            print("IPADOS_COMPATIBLE_P12_PUBLISH=PASS")
            print("IPADOS_COMPATIBLE_P12_PATH=" + IPADOS_TARGET)
            print("IPADOS_COMPATIBLE_P12_SIZE=" + str(len(p12_data)))
            print("IPADOS_COMPATIBLE_P12_SHA256=" + hashlib.sha256(p12_data).hexdigest())
            print("IPADOS_COMPATIBLE_P12_PBES=3DES_SHA1")
            print("EXISTING_P12_AND_PASSWORD_FILE=UNCHANGED")
            print("IMPORT_PASSWORD=SAME_EXISTING_PASSWORD_TXT")
        else:
            print("P12_PASSWORD_FILE_PUBLISH=PASS")
            print("P12_PASSWORD_FILE_PATH=" + PASSWORD_TARGET)
            print("PASSWORD_FILE_CONTAINS_SECRET=YES")
            print("EXISTING_P12_FILE_CHANGED=NO")
        if info is not None:
            print("FILEBROWSER_OWNER_UID=" + str(info["uid"]))
            print("FILE_MODE=0600")
        print("CLOUDFLARE_MUTATIONS=NONE")
        print("OTHER_TRUENAS_PATHS_MUTATIONS=NONE")


if __name__ == "__main__":
    try:
        main()
    except Block as err:
        print("BLOCK=" + str(err), file=sys.stderr)
        sys.exit(2)
    except Exception as err:
        print("BLOCK=UNEXPECTED_" + type(err).__name__, file=sys.stderr)
        sys.exit(2)
    finally:
        print("=== DONE ===")
        print("=== TERMINAL STAYS OPEN ===")
