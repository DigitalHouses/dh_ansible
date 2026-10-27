#!/usr/bin/env python3
"""One-time PVE site runtime MQTT commissioning from existing encrypted Fleet Vault."""
import base64
import getpass
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import yaml

SITE = "serzhan-home"
REPO = "DigitalHouses/dh_ansible_fleet_detail"
TARGET = Path("/var/lib/ansible/runtime") / (SITE + ".yml")
VAULT = "secrets/fleet.vault.yml"
WORKER = r'''
import base64, json, sys, yaml
from ansible.parsing.vault import VaultLib, VaultSecret
try:
    data = json.loads(sys.stdin.read())
    plain = VaultLib([("default", VaultSecret(data["master"].encode()))]).decrypt(
        base64.b64decode(data["cipher"], validate=True))
    record = yaml.safe_load(plain)
    if "fleet_secrets" in record:
        site_secrets = record["fleet_secrets"]["sites"]["serzhan-home"]
    else:
        passport = record["site_passport"]
        if passport["site_id"] != "serzhan-home":
            raise ValueError("SITE_INVALID")
        site_secrets = passport["site_secrets"]
    mqtt = site_secrets["plex_agent"]["mqtt"]
    username, password = mqtt["username"], mqtt["password"]
    if not all(isinstance(v, str) and 0 < len(v) <= 512 and
               "\\n" not in v and "\\r" not in v
               for v in [username, password]):
        raise ValueError("MQTT_INVALID")
    print(json.dumps({"username": username, "password": password}))
except Exception:
    print("BLOCKED:VAULT_DECRYPTION_OR_MQTT_FAILED", file=sys.stderr)
    sys.exit(2)
'''


def require(value, reason):
    if not value:
        raise RuntimeError(reason)


def run(argv, stdin=None):
    p = subprocess.run(argv, input=stdin, capture_output=True, text=True,
                       timeout=120, check=False)
    require(p.returncode == 0, "SUBPROCESS_FAILED_" + Path(argv[0]).name.upper())
    return p.stdout.strip()


def file_guard(path, owner=0, group=0, mode=0o600):
    st = path.lstat()
    require(stat.S_ISREG(st.st_mode) and st.st_uid == owner and
            st.st_gid == group and stat.S_IMODE(st.st_mode) == mode and
            st.st_nlink == 1, "RUNTIME_FILE_UNSAFE")


def update_runtime(mqtt):
    root = TARGET.parent
    if not root.exists():
        root.mkdir(mode=0o700, parents=True)
    st = root.lstat()
    require(stat.S_ISDIR(st.st_mode) and st.st_uid == 0 and st.st_gid == 0 and
            stat.S_IMODE(st.st_mode) == 0o700, "RUNTIME_DIR_UNSAFE")
    if TARGET.exists() or TARGET.is_symlink():
        file_guard(TARGET)
        envelope = yaml.safe_load(TARGET.read_text("utf-8"))
        require(envelope["runtime_secrets"]["schema_version"] == 1 and
                envelope["runtime_secrets"]["site_id"] == SITE,
                "EXISTING_SITE_RUNTIME_MISMATCH")
        site_secrets = envelope["runtime_secrets"]["site_secrets"]
        require(isinstance(site_secrets, dict), "RUNTIME_NAMESPACE_INVALID")
    else:
        site_secrets = {}
        envelope = {"runtime_secrets": {"schema_version": 1,
                    "site_id": SITE, "site_secrets": site_secrets}}
    app = site_secrets.setdefault("plex_agent", {})
    require(isinstance(app, dict), "RUNTIME_APP_NAMESPACE_INVALID")
    prior = app.get("mqtt")
    require(prior in (None, mqtt), "EXISTING_MQTT_RUNTIME_CONFLICT")
    app["mqtt"] = mqtt
    content = yaml.safe_dump(envelope, allow_unicode=True,
                             sort_keys=False).encode("utf-8")
    if TARGET.exists() and TARGET.read_bytes() == content:
        print("PVE_MQTT_RUNTIME=UNCHANGED")
        return
    fd, temp = tempfile.mkstemp(prefix=".dh-runtime-", dir=root)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temp, TARGET)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)
    file_guard(TARGET)
    print("PVE_MQTT_RUNTIME=READY")


def main():
    require(os.geteuid() == 0 and Path("/etc/pve/user.cfg").is_file(),
            "PVE_ROOT_REQUIRED")
    require(run(["/usr/sbin/pct", "status", "1231"]) == "status: running",
            "SITE_RUNNER_NOT_RUNNING")
    require(run(["/usr/sbin/pct", "exec", "1231", "--", "hostname"]) ==
            "ansible-serzhan-home", "RUNNER_IDENTITY_MISMATCH")
    name = "repos/" + REPO + "/contents/" + VAULT + "?ref=main"
    item = json.loads(run(["/usr/bin/gh", "api", name]))
    require(item.get("type") == "file" and
            item.get("encoding") == "base64", "FLEET_VAULT_SOURCE_INVALID")
    source = base64.b64decode(item["content"], validate=False)
    digest = hashlib.sha1(b"blob " + str(len(source)).encode() + b"\0" +
                          source).hexdigest()
    require(source.startswith(b"$ANSIBLE_VAULT;") and digest == item["sha"],
            "FLEET_ENCRYPTED_SOURCE_INVALID")
    print("FLEET_ENCRYPTED_SOURCE=VERIFIED", flush=True)
    with open("/dev/tty", "w", encoding="utf-8") as tty:
        master = getpass.getpass("Existing Fleet Vault master password: ",
                                 stream=tty)
    require(bool(master), "MASTER_PASSWORD_EMPTY")
    request = json.dumps({
        "master": master, "cipher": base64.b64encode(source).decode("ascii")})
    raw = run(["/usr/sbin/pct", "exec", "1231", "--",
               "/opt/ansible/venv/bin/python", "-c", WORKER], stdin=request)
    result = json.loads(raw)
    mqtt = {"username": result["username"], "password": result["password"]}
    update_runtime(mqtt)


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, KeyError, TypeError, ValueError,
            EOFError, yaml.YAMLError, subprocess.TimeoutExpired) as e:
        print("PVE_MQTT_RUNTIME=BLOCKED_" + (
              str(e) if isinstance(e, RuntimeError) else type(e).__name__),
              file=sys.stderr)
        sys.exit(2)
