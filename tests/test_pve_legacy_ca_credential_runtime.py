"""Real local TLS tests for PVE 8 CA without Key Usage and Task 06 client.

No connection to production PVE or Semaphore, no actual credentials.
Proof: validated TLS chain + hostname + fingerprint before both secret-bearing
POST and cookie-bearing GET. Only VERIFY_X509_STRICT is relaxed.
"""
from __future__ import annotations

import hashlib
import http.server
import json
from pathlib import Path
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
CLIENT = ROOT / "scripts/pve-https-credential-check.py"
PLAY = ROOT / "playbooks/check-site-credentials.yml"


class API(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        self.server.requests_seen += 1
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode()
        from urllib.parse import parse_qs
        values = parse_qs(body)
        assert self.path == "/api2/json/access/ticket"
        good = (values.get("username") == ["root@pam"]
                and values.get("password") == ["dummy-password"])
        self.send_response(200 if good else 401)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        if good:
            self.wfile.write(json.dumps({
                "data": {"ticket": "PVE:dummy:0123456789012345"}
            }).encode())
        else:
            self.wfile.write(b'{"errors":{}}')

    def do_GET(self):
        self.server.requests_seen += 1
        good = (
            self.path == "/api2/json/nodes"
            and self.headers.get("Cookie") == "PVEAuthCookie=PVE:dummy:0123456789012345"
        )
        self.send_response(200 if good else 403)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        if good:
            self.wfile.write(b'{"data":[{"node":"pve"}]}')


class LegacyCaTLS(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="pve-legacy-ca-")
        d = Path(cls.temp.name)
        ca_cfg = d / "ca.cnf"
        ca_cfg.write_text(
            "[req]\n"
            "prompt=no\n"
            "distinguished_name=req_dn\n"
            "x509_extensions=ca\n"
            "[req_dn]\n"
            "CN=Legacy PVE root CA\n"
            "[ca]\n"
            "basicConstraints=critical,CA:TRUE\n"
            "subjectKeyIdentifier=hash\n"
            "authorityKeyIdentifier=keyid:always\n"
            "# Key Usage deliberately absent, as on PVE 8.4\n"
        )
        leaf_cfg = d / "leaf.cnf"
        leaf_cfg.write_text(
            "[leaf]\n"
            "basicConstraints=critical,CA:FALSE\n"
            "keyUsage=digitalSignature,keyEncipherment\n"
            "extendedKeyUsage=serverAuth\n"
            "subjectAltName=IP:127.0.0.1,DNS:localhost\n"
        )
        def openssl(*args):
            subprocess.run(["openssl", *map(str, args)], check=True,
                           capture_output=True, timeout=12)

        openssl("req", "-x509", "-newkey", "rsa:2048", "-nodes",
                "-keyout", d / "ca.key", "-out", d / "ca.pem",
                "-days", "1", "-config", ca_cfg)
        openssl("req", "-newkey", "rsa:2048", "-nodes",
                "-keyout", d / "leaf.key", "-out", d / "leaf.csr",
                "-days", "1", "-subj", "/CN=localhost")
        openssl("x509", "-req", "-in", d / "leaf.csr",
                "-CA", d / "ca.pem", "-CAkey", d / "ca.key",
                "-CAcreateserial", "-out", d / "leaf.pem",
                "-days", "1", "-extfile", leaf_cfg, "-extensions", "leaf")

        cls.ca = d / "ca.pem"
        cert_pem = (d / "leaf.pem").read_text()
        cert_der = ssl.PEM_cert_to_DER_cert(cert_pem)
        digest = hashlib.sha256(cert_der).hexdigest().upper()
        cls.fingerprint = ":".join(digest[i:i+2] for i in range(0, 64, 2))
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), API)
        cls.server.requests_seen = 0
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.load_cert_chain(str(d / "leaf.pem"), str(d / "leaf.key"))
        cls.server.socket = tls.wrap_socket(cls.server.socket, server_side=True)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=3)
        cls.temp.cleanup()

    def config(self, **overrides):
        config = {
            "ip": "127.0.0.1",
            "api_hostname": "127.0.0.1",
            "port": self.port,
            "ca_file": str(self.ca),
            "fingerprint": self.fingerprint,
            "node": "pve",
            "password": "dummy-password",
        }
        config.update(overrides)
        return config

    def run_check(self, **overrides):
        config = self.config(**overrides)
        p = subprocess.run(
            [sys.executable, str(CLIENT)],
            input=json.dumps(config), capture_output=True,
            text=True, timeout=12, check=False,
        )
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertNotIn(config["password"], p.stdout + p.stderr)
        self.assertEqual(p.stderr, "")
        return p.stdout.strip()

    def test_strict_ssl_rejects_synthetic_old_ca(self):
        context = ssl.create_default_context(cafile=str(self.ca))
        context.verify_flags |= ssl.VERIFY_X509_STRICT
        with self.assertRaises(ssl.SSLCertVerificationError):
            with socket.create_connection(("127.0.0.1", self.port), timeout=4) as raw:
                with context.wrap_socket(raw, server_hostname="127.0.0.1"):
                    pass

    def test_legacy_ca_succeeds_with_full_ca_hostname_and_pin(self):
        self.assertEqual(self.run_check(), "READY")

    def test_invalid_password_classified_without_printing_secret(self):
        self.assertEqual(self.run_check(password="dummy-not-the-password"),
                         "AUTH_FAILED")

    def test_pin_mismatch_blocks_before_sending_password(self):
        previous = self.server.requests_seen
        wrong = "00:" + self.fingerprint[3:]
        self.assertEqual(self.run_check(fingerprint=wrong),
                         "PVE_TLS_IDENTITY_MISMATCH")
        self.assertEqual(previous, self.server.requests_seen)

    def test_hostname_mismatch_is_rejected(self):
        self.assertEqual(self.run_check(api_hostname="not-pve.local"),
                         "PVE_TLS_CERT_UNTRUSTED")

    def test_wrong_node_is_distinguished(self):
        self.assertEqual(self.run_check(node="wrong-node"),
                         "PVE_NODE_MISMATCH")

    def test_playbook_uses_repo_client_with_stdin_and_no_log(self):
        play = yaml.safe_load(PLAY.read_text())[0]
        task = next(t for t in play["tasks"]
                    if t["name"].startswith("Check root@pam and PVE node"))
        command = task["ansible.builtin.command"]
        self.assertTrue(task["no_log"])
        self.assertFalse(task["changed_when"])
        self.assertEqual(command["stdin_add_newline"], False)
        self.assertIn("password", command["stdin"])
        self.assertIn("to_json", command["stdin"])
        self.assertIn("scripts/pve-https-credential-check.py", command["argv"][1])
        self.assertNotIn("DH_PVE_ROOT_PASSWORD", str(command["argv"]))
        code = CLIENT.read_text()
        self.assertIn("ssl.create_default_context", code)
        self.assertIn("ctx.verify_flags &= ~ssl.VERIFY_X509_STRICT", code)
        self.assertIn("ctx.verify_mode != ssl.CERT_REQUIRED", code)
        self.assertIn("not ctx.check_hostname", code)
        self.assertNotIn("CERT_NONE", code)
        self.assertNotIn("check_hostname = False", code)


if __name__ == "__main__":
    unittest.main()
