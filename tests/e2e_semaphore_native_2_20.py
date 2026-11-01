#!/usr/bin/env python3
"""Isolated Semaphore 2.20 native Variable Group API E2E, dummy data only.

Run as a nonroot user on the site runner. Uses temporary HTTPS, random local
port and disposable SQLite. Does not connect to production port 3000.
"""
from __future__ import annotations

import base64
from http.cookiejar import CookieJar
import json
import os
from pathlib import Path
import socket
import ssl
import subprocess
import tempfile
import time
import urllib.error
import urllib.request

SEMAPHORE = "/usr/bin/semaphore"
DUMMY = {
    "admin": "DUMMY-ADMIN-ONLY-15152",
    "pve_old": "DUMMY-PVE-OLD-18559",
    "pve_new": "DUMMY-PVE-NEW-17793",
    "mqtt": "DUMMY-MQTT-13941",
}


def must(value, label):
    if not value:
        raise RuntimeError(label)


def run(*argv):
    result = subprocess.run(argv, capture_output=True, timeout=45)
    must(result.returncode == 0, "TEST_SETUP_FAILED: " + argv[0])


def port_number():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def make_api(port, cert):
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(CookieJar()),
        urllib.request.HTTPSHandler(
            context=ssl.create_default_context(cafile=str(cert))),
        urllib.request.ProxyHandler({}))

    def request(method, path, data=None):
        raw = json.dumps(data).encode() if data is not None else None
        req = urllib.request.Request(
            f"https://127.0.0.1:{port}/api{path}",
            data=raw, method=method,
            headers={"Content-Type": "application/json", "Accept": "application/json"})
        with opener.open(req, timeout=10) as response:
            payload = response.read(1_000_000)
            return json.loads(payload) if payload else None

    return request


def validate(api, root):
    api("POST", "/auth/login", {"auth": "test", "password": DUMMY["admin"]})
    project = api("POST", "/projects", {
        "name": "native-credentials-e2e", "max_parallel_tasks": 1,
        "alert": False, "demo": False})
    pid = project["id"]
    must(type(pid) is int and pid > 0, "PROJECT_INVALID")
    base = f"/project/{pid}/environment"
    group = api("POST", base, {
        "name": "Empty", "project_id": pid, "json": "{}", "env": "{}"})
    gid = group["id"]
    must(type(gid) is int and gid > 0, "GROUP_INVALID")
    detail = base + "/" + str(gid)

    body = {
        "id": gid, "project_id": pid, "name": "Empty", "json": "{}",
        "env": json.dumps({"DH_MQTT_USERNAME": "dummy-user"}),
        "secrets": [
            {"name": "DH_PVE_ROOT_PASSWORD", "type": "var",
             "operation": "create", "secret": DUMMY["pve_old"]},
            {"name": "DH_MQTT_PASSWORD", "type": "var",
             "operation": "create", "secret": DUMMY["mqtt"]},
        ],
    }
    api("PUT", detail, body)
    before = api("GET", detail)
    items = {x["name"]: x for x in before["secrets"]}
    must(set(items) == {"DH_PVE_ROOT_PASSWORD", "DH_MQTT_PASSWORD"},
         "SECRET_CREATE_INCOMPLETE")
    must(all(x["type"] == "var" for x in items.values()), "SECRET_TYPE_WRONG")
    must(json.loads(before["env"])["DH_MQTT_USERNAME"] == "dummy-user",
         "NONSENSITIVE_VALUE_MISSING")
    must(not any(x in json.dumps(before) for x in DUMMY.values()),
         "API_LEAKED_SECRET")
    print("NATIVE_VAR_CREATE=PASS", flush=True)

    # Change only PVE password. Other secret and login must remain intact.
    api("PUT", detail, {
        "id": gid, "project_id": pid, "name": "Empty",
        "json": before["json"], "env": before["env"],
        "secrets": [{
            "id": items["DH_PVE_ROOT_PASSWORD"]["id"],
            "name": "DH_PVE_ROOT_PASSWORD", "type": "var",
            "operation": "update", "secret": DUMMY["pve_new"],
        }],
    })
    after = api("GET", detail)
    newer = {x["name"]: x for x in after["secrets"]}
    must(set(newer) == set(items), "PARTIAL_UPDATE_REMOVED_OTHER_SECRET")
    must(all(newer[k]["id"] == items[k]["id"] for k in items),
         "PARTIAL_UPDATE_CHANGED_SECRET_ID")
    must(after["env"] == before["env"], "PARTIAL_UPDATE_CHANGED_USERNAME")
    must(not any(x in json.dumps(after) for x in DUMMY.values()),
         "API_LEAKED_UPDATED_SECRET")
    print("NATIVE_VAR_PARTIAL_KEEP=PASS", flush=True)

    # Verify no dummy secret is present as plaintext in disposable SQLite.
    for file in (root / "database.sqlite", root / "database.sqlite-wal"):
        if file.exists():
            raw = file.read_bytes()
            must(not any(value.encode() in raw for value in DUMMY.values()),
                 "TEST_SQLITE_HAS_PLAINTEXT_SECRET")
    print("NATIVE_VAR_SQLITE_ENCRYPTION=PASS", flush=True)


def main():
    must(os.geteuid() != 0, "RUN_AS_NONROOT_USER")
    must(Path(SEMAPHORE).is_file(), "SEMAPHORE_BINARY_NOT_INSTALLED")
    must(not Path("/etc/pve/user.cfg").exists(), "DO_NOT_RUN_ON_PVE")
    with tempfile.TemporaryDirectory(prefix="dh-native-credentials-e2e-") as tmp:
        root = Path(tmp)
        port = port_number()
        run("openssl", "req", "-x509", "-newkey", "ec",
            "-pkeyopt", "ec_paramgen_curve:P-256", "-nodes", "-days", "1",
            "-keyout", str(root / "key.pem"), "-out", str(root / "cert.pem"),
            "-subj", "/CN=127.0.0.1", "-addext", "subjectAltName=IP:127.0.0.1")
        (root / "tasks").mkdir()
        key = lambda: base64.b64encode(os.urandom(32)).decode()
        config = {
            "dialect": "sqlite",
            "sqlite": {"host": str(root / "database.sqlite")},
            "port": ":" + str(port), "interface": "127.0.0.1",
            "web_host": f"https://127.0.0.1:{port}",
            "tmp_path": str(root / "tasks"),
            "cookie_hash": key(), "cookie_encryption": key(),
            "access_key_encryption": key(),
            "tls": {"enabled": True, "cert_file": str(root / "cert.pem"),
                    "key_file": str(root / "key.pem")},
        }
        config_path = root / "config.json"
        config_path.write_text(json.dumps(config))
        run(SEMAPHORE, "migrate", "--config", str(config_path))
        run(SEMAPHORE, "users", "add", "--config", str(config_path),
            "--admin", "--login", "test", "--email", "test@example.invalid",
            "--name", "Temporary Test", "--password", DUMMY["admin"])
        with (root / "server.log").open("wb") as out:
            server = subprocess.Popen(
                [SEMAPHORE, "server", "--config", str(config_path)],
                stdout=out, stderr=subprocess.STDOUT)
            try:
                api = make_api(port, root / "cert.pem")
                for _ in range(80):
                    must(server.poll() is None, "TEST_SERVER_EXITED")
                    try:
                        api("GET", "/ping")
                        break
                    except (urllib.error.URLError, TimeoutError):
                        time.sleep(0.25)
                else:
                    raise RuntimeError("TEST_SERVER_NOT_READY")
                validate(api, root)
                print("SEMAPHORE_BETA_ISOLATED_NATIVE_E2E=PASS", flush=True)
            finally:
                if server.poll() is None:
                    server.terminate()
                    try:
                        server.wait(timeout=8)
                    except subprocess.TimeoutExpired:
                        server.kill()
                        server.wait(timeout=8)


if __name__ == "__main__":
    main()
