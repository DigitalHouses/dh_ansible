#!/usr/bin/env python3
"""Isolated real Semaphore 2.19.16 credential enrollment E2E.

Creates a loopback-only temporary Semaphore server and SQLite DB.
Never connects to or changes the existing site Semaphore service.
Execute directly on a test runner with semaphore and openssl installed.
"""
import base64
import contextlib
import http.cookiejar
import json
from pathlib import Path
import os
import runpy
import secrets
import socket
import ssl
import subprocess
import tempfile
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
app = runpy.run_path(str(ROOT / "scripts/semaphore-obtain-credentials.py"),
                     run_name="isolated_native_e2e")


def test():
    with tempfile.TemporaryDirectory(prefix="dh-native-semaphore-e2e-") as tmp:
        folder = Path(tmp)
        cert, key = folder / "cert.pem", folder / "key.pem"
        subprocess.run([
            "openssl", "req", "-x509", "-newkey", "ec",
            "-pkeyopt", "ec_paramgen_curve:P-256", "-nodes", "-days", "1",
            "-keyout", str(key), "-out", str(cert),
            "-subj", "/CN=127.0.0.1",
            "-addext", "subjectAltName=IP:127.0.0.1",
        ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        random_key = lambda: base64.b64encode(secrets.token_bytes(32)).decode()
        database = folder / "test-database.sqlite"
        settings = {
            "dialect": "sqlite",
            "sqlite": {"host": str(database)},
            "port": ":" + str(port),
            "interface": "127.0.0.1",
            "web_host": f"https://127.0.0.1:{port}",
            "tmp_path": str(folder / "tmp"),
            "cookie_hash": random_key(),
            "cookie_encryption": random_key(),
            "access_key_encryption": random_key(),
            "tls": {"enabled": True, "cert_file": str(cert), "key_file": str(key)},
        }
        (folder / "tmp").mkdir()
        config = folder / "config.json"
        config.write_text(json.dumps(settings))
        config.chmod(0o600)
        admin_password = secrets.token_urlsafe(30)
        subprocess.run([
            "/usr/bin/semaphore", "users", "add", "--config", str(config),
            "--admin", "--login", "pilot-admin", "--name", "Pilot Admin",
            "--email", "pilot@example.invalid", "--password", admin_password,
        ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        service = subprocess.Popen(
            ["/usr/bin/semaphore", "server", "--config", str(config)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        try:
            client = urllib.request.build_opener(
                urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
                urllib.request.HTTPSHandler(
                    context=ssl.create_default_context(cafile=str(cert))),
                urllib.request.ProxyHandler({}))
            base = f"https://127.0.0.1:{port}/api"

            def request(method, path, body=None):
                payload = json.dumps(body).encode() if body is not None else None
                req = urllib.request.Request(
                    base + path, method=method, data=payload,
                    headers={"Content-Type": "application/json",
                             "Accept": "application/json"})
                with client.open(req, timeout=5) as response:
                    data = response.read()
                    return json.loads(data) if data else None

            for _ in range(100):
                if service.poll() is not None:
                    raise AssertionError("Isolated Semaphore process exited")
                try:
                    request("POST", "/auth/login",
                            {"auth": "pilot-admin", "password": admin_password})
                    break
                except Exception:
                    time.sleep(0.1)
            else:
                raise AssertionError("Isolated Semaphore login failed")
            print("ISOLATED_API_LOGIN=PASS")
            project = request("POST", "/projects", {
                "name": "serzhan-lab", "max_parallel_tasks": 1,
                "alert": False, "demo": False})
            assert project["name"] == "serzhan-lab", "Project creation"
            prefix = "/project/" + str(project["id"])

            class IsolatedAPI:
                def request(self, method, path, body=None):
                    return request(method, path, body)

            api = IsolatedAPI()
            result = app["enroll"](api, "serzhan-lab", {
                "github_provision_token": "DUMMY-GH-001",
                "mqtt_username": "dummy-user",
                "mqtt_password": "DUMMY-MQTT-001",
                "pve_api_audit_token": "DUMMY-PVE-AUDIT",
                "pve_api_write_token": "DUMMY-PVE-WRITE",
                "cloudflare_api_token": "DUMMY-CF-001",
            })
            assert all(v == "CREATED" for v in result.values()), result
            print("SIX_FIELDS_CREATED=PASS")
            result = app["enroll"](api, "serzhan-lab", {
                "mqtt_password": "DUMMY-MQTT-002",
            })
            assert result["mqtt_password"] == "UPDATED", result
            assert all(v == "KEEP" for k, v in result.items()
                       if k != "mqtt_password"), result
            print("ONLY_MQTT_PASSWORD_UPDATED=PASS")
            result = app["enroll"](api, "serzhan-lab", {
                "mqtt_username": "new-dummy-user",
            })
            assert result["mqtt_username"] == "UPDATED", result
            print("ONLY_MQTT_USERNAME_UPDATED=PASS")
            groups = request("GET", prefix + "/environment?sort=name&order=asc")
            var_records = [g for g in groups
                           if g.get("name") == app["SITE_VARIABLE_GROUP"]]
            assert len(var_records) == 1, "Variable Group duplicate or missing"
            group_id = var_records[0]["id"]
            detail = request("GET", prefix + "/environment/" + str(group_id))
            assert json.loads(detail["env"])["DH_MQTT_USERNAME"] == "new-dummy-user"
            assert len(detail["secrets"]) == 5, "Unexpected secret count"
            print("NATIVE_METADATA_VERIFIED=PASS")
            # The SQLite payload must not contain any literal dummy secret.
            blobs = b"".join(p.read_bytes() for p in folder.glob("test-database.sqlite*"))
            for literal in (
                b"DUMMY-GH-001", b"DUMMY-MQTT-001", b"DUMMY-MQTT-002",
                b"DUMMY-PVE-AUDIT", b"DUMMY-PVE-WRITE", b"DUMMY-CF-001",
            ):
                assert literal not in blobs, "Plaintext secret found in temporary SQLite"
            print("SQLITE_SECRETS_ENCRYPTED=PASS")
        finally:
            service.terminate()
            with contextlib.suppress(subprocess.TimeoutExpired):
                service.wait(timeout=5)
            if service.poll() is None:
                service.kill()
                service.wait()
    print("ISOLATED_TEST_RESOURCES_REMOVED=PASS")
    print("PRODUCTION_SEMAPHORE_WRITES=NONE")


if __name__ == "__main__":
    test()
