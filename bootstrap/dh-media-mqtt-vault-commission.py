#!/usr/bin/env python3
"""PVE root: add this home's MQTT credentials to existing encrypted Fleet Vault.

Prompts exclusively on /dev/tty. No plaintext files, CLI credential arguments,
terminal echo for passwords, or output containing user-supplied secrets.
Requires existing PVE root gh authentication and CT1231 Ansible Vault library.
"""
import base64
import getpass
import hashlib
import json
import os
import re
import subprocess
import sys

REPO = "DigitalHouses/dh_ansible_fleet_detail"
VAULT_PATH = "secrets/fleet.vault.yml"
SITE = "serzhan-home"
CTID = "1231"
RUNNER_HOST = "ansible-serzhan-home"
ROOT = "/usr/sbin/pct"
GH = "/usr/bin/gh"
WORKER = r'''
import base64
import copy
import json
import sys
from ansible.parsing.vault import VaultLib, VaultSecret
import yaml

def fail(reason):
    print("BLOCKED:" + reason, file=sys.stderr)
    sys.exit(2)

try:
    request = json.loads(sys.stdin.read())
    source = base64.b64decode(request["cipher"], validate=True)
    master = request["master"].encode("utf-8")
    old = VaultLib([("default", VaultSecret(master))])
    plain = old.decrypt(source)
    document = yaml.safe_load(plain)
    if not isinstance(document, dict):
        fail("VAULT_DOCUMENT_INVALID")
    if "fleet_secrets" in document:
        sites = document["fleet_secrets"].get("sites", {})
        if not isinstance(sites, dict) or "serzhan-home" not in sites:
            fail("SITE_NAMESPACE_MISSING")
        target = sites["serzhan-home"]
    elif "site_passport" in document:
        passport = document["site_passport"]
        if passport.get("site_id") != "serzhan-home":
            fail("SITE_ID_MISMATCH")
        target = passport["site_secrets"]
    else:
        fail("VAULT_SCHEMA_UNSUPPORTED")
    if not isinstance(target, dict):
        fail("SITE_SECRETS_INVALID")
    app = target.setdefault("plex_agent", {})
    if not isinstance(app, dict):
        fail("PLEX_AGENT_NAMESPACE_INVALID")
    mqtt = app.setdefault("mqtt", {})
    if not isinstance(mqtt, dict):
        fail("MQTT_NAMESPACE_INVALID")
    username = request["username"]
    password = request["password"]
    if any(mqtt.get(k) not in (None, v)
           for k, v in (("username", username), ("password", password))):
        fail("MQTT_ALREADY_CONFIGURED_DIFFERENTLY")
    if mqtt.get("username") == username and mqtt.get("password") == password:
        print(json.dumps({"state": "UNCHANGED"}))
        sys.exit(0)
    mqtt["username"] = username
    mqtt["password"] = password
    serialized = yaml.safe_dump(
        document, allow_unicode=True, sort_keys=False).encode("utf-8")
    ciphertext = old.encrypt(serialized, secret=VaultSecret(master))
    if yaml.safe_load(old.decrypt(ciphertext)) != document:
        fail("ENCRYPTED_ROUNDTRIP_MISMATCH")
    print(json.dumps({
        "state": "UPDATED",
        "cipher": base64.b64encode(ciphertext).decode("ascii"),
    }))
except Exception:
    fail("VAULT_PROCESSING_FAILED")
'''

def run(argv, *, stdin=None):
    p = subprocess.run(argv, input=stdin, text=True, capture_output=True,
                       timeout=120, check=False)
    if p.returncode:
        raise RuntimeError("COMMAND_FAILED_" + os.path.basename(argv[0]))
    return p.stdout.strip()


def require(value, message):
    if not value:
        raise RuntimeError(message)


def prompt_from_tty():
    with open("/dev/tty", "r+", encoding="utf-8") as tty:
        tty.write("MQTT username: ")
        tty.flush()
        username = tty.readline().rstrip("\r\n")
        password = getpass.getpass("MQTT password: ", stream=tty)
        master = getpass.getpass("Existing Fleet Vault master password: ",
                                 stream=tty)
    require(0 < len(username) <= 128 and not any(ord(c) < 32 for c in username),
            "MQTT_USERNAME_INVALID")
    require(0 < len(password) <= 512 and not any(ord(c) < 32 for c in password),
            "MQTT_PASSWORD_INVALID")
    require(bool(master), "VAULT_MASTER_EMPTY")
    return username, password, master


def main():
    require(os.geteuid() == 0 and os.path.isfile("/etc/pve/user.cfg"),
            "PVE_ROOT_REQUIRED")
    require(run([ROOT, "status", CTID]) == "status: running",
            "ANSIBLE_CONTAINER_NOT_RUNNING")
    require(run([ROOT, "exec", CTID, "--", "/usr/bin/hostname"]) ==
            RUNNER_HOST, "ANSIBLE_CONTAINER_IDENTITY_MISMATCH")
    url = "repos/" + REPO + "/contents/" + VAULT_PATH + "?ref=main"
    document = json.loads(run([GH, "api", url]))
    require(document.get("type") == "file" and
            document.get("encoding") == "base64", "VAULT_GITHUB_SOURCE_INVALID")
    source = base64.b64decode(document["content"], validate=False)
    require(source.startswith(b"$ANSIBLE_VAULT;"), "SOURCE_NOT_ENCRYPTED_VAULT")
    sha = hashlib.sha1(b"blob " + str(len(source)).encode() + b"\0" +
                       source).hexdigest()
    require(document.get("sha") == sha, "SOURCE_GIT_SHA_MISMATCH")
    print("SOURCE_ENCRYPTED_FLEET_VAULT=VERIFIED")
    username, password, master = prompt_from_tty()
    payload = json.dumps({
        "cipher": base64.b64encode(source).decode("ascii"),
        "username": username, "password": password, "master": master,
    })
    result = json.loads(run(
        [ROOT, "exec", CTID, "--", "/opt/ansible/venv/bin/python",
         "-c", WORKER], stdin=payload))
    if result["state"] == "UNCHANGED":
        print("MQTT_FLEET_VAULT=UNCHANGED")
        return
    require(result.get("state") == "UPDATED", "VAULT_WORKER_RESULT_INVALID")
    candidate = base64.b64decode(result["cipher"], validate=True)
    require(candidate.startswith(b"$ANSIBLE_VAULT;"),
            "VAULT_CANDIDATE_NOT_ENCRYPTED")
    body = json.dumps({
        "message": "chore(fleet): commission serzhan-home Plex Agent MQTT (encrypted)",
        "content": base64.b64encode(candidate).decode("ascii"),
        "sha": sha, "branch": "main",
    })
    saved = json.loads(run(
        [GH, "api", "--method", "PUT", "repos/" + REPO +
         "/contents/" + VAULT_PATH,
         "-H", "Accept: application/vnd.github+json",
         "--input", "-"], stdin=body))
    updated_sha = hashlib.sha1(b"blob " + str(len(candidate)).encode() +
                               b"\0" + candidate).hexdigest()
    require(saved.get("content", {}).get("sha") == updated_sha,
            "GITHUB_WRITE_NOT_CONFIRMED")
    print("MQTT_FLEET_VAULT=ENCRYPTED_AND_COMMITTED")
    print("COMMIT=" + saved["commit"]["sha"])


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, ValueError, KeyError, subprocess.TimeoutExpired):
        print("MQTT_VAULT_COMMISSION=BLOCKED; NO_PLAINTEXT_FILES_WRITTEN",
              file=sys.stderr)
        sys.exit(2)
