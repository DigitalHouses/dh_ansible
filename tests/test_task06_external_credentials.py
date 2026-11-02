"""Read-only site credential verification contracts for Task 06.

All MQTT tests use a fake socket, with synthetic credentials only.
No Cloudflare API request and no site-specific broker request in CI.
"""
import importlib.util
import io
import json
from pathlib import Path
import sys
from contextlib import redirect_stdout

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/mqtt-credential-check.py"
PLAYBOOK = ROOT / "playbooks/check-site-credentials.yml"
PLAYS = yaml.safe_load(PLAYBOOK.read_text())


def module():
    spec = importlib.util.spec_from_file_location("dh_test_mqtt_check", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakeSocket:
    def __init__(self, reply):
        self.reply = reply
        self.sent = b""

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def settimeout(self, timeout):
        assert timeout <= 10

    def sendall(self, data):
        self.sent = data

    def recv(self, count):
        result = self.reply[:count]
        self.reply = self.reply[count:]
        return result


@pytest.mark.parametrize("reply,status", [
    (b"\x20\x02\x00\x00", "READY"),
    (b"\x20\x02\x00\x04", "AUTH_FAILED"),
    (b"\x20\x02\x00\x05", "AUTH_FAILED"),
    (b"\x20\x02\x00\x03", "UNREACHABLE"),
    (b"\x20\x02\x00\x01", "PROTOCOL_ERROR"),
    (b"\x20\x02\x00\xff", "PROTOCOL_ERROR"),
    (b"\x30\x02\x00\x00", "PROTOCOL_ERROR"),
])
def test_mqtt_connect_no_pubsub_or_credential_output(monkeypatch, reply, status):
    m = module()
    sock = FakeSocket(reply)
    monkeypatch.setattr(m.socket, "create_connection",
                        lambda endpoint, timeout: sock)
    output = io.StringIO()
    with redirect_stdout(output):
        result = m.check("broker.example.test", "demo-user", "dummy-secret")
    assert result == status
    assert output.getvalue() == ""
    assert sock.sent.startswith(b"\x10")
    assert b"demo-user" in sock.sent
    assert b"dummy-secret" in sock.sent
    assert b"\x00\x04MQTT\x04\xC2" in sock.sent
    assert sock.sent[0] == 0x10  # CONNECT, no PUB/SUB


@pytest.mark.parametrize("host", [
    "", "mqtt://broker.example", "broker.example:1883", "-bad.local",
    "bad..local", "bad.local/", "bad local", "broker.example.",
])
def test_mqtt_rejects_invalid_endpoint_without_network(host, monkeypatch):
    m = module()
    def no_socket(*args, **kwargs):
        raise AssertionError("Should not connect for invalid host")
    monkeypatch.setattr(m.socket, "create_connection", no_socket)
    assert m.check(host, "dummy-user", "dummy-pass") == "INVALID_BROKER_HOST"


def test_mqtt_exception_classified_with_no_secret_leak(monkeypatch):
    m = module()
    def denied(*args, **kwargs):
        raise ConnectionRefusedError("dummy-pass secret not for exception output")
    monkeypatch.setattr(m.socket, "create_connection", denied)
    assert m.check("127.0.0.1", "dummy-user", "dummy-pass") == "UNREACHABLE"


def test_task06_is_read_only_for_mqtt_and_cloudflare_and_no_cli_secret():
    play = PLAYS[0]
    mqtt = next(t for t in play["tasks"] if t["name"].startswith("Authenticate to the configured MQTT broker"))
    assert mqtt["no_log"] is True
    argv = mqtt["ansible.builtin.command"]["argv"]
    assert argv[0] == "python3"
    assert "scripts/mqtt-credential-check.py" in argv[1]
    assert "DH_MQTT_PASSWORD" not in str(argv)
    assert mqtt["ansible.builtin.command"]["stdin_add_newline"] is False
    assert mqtt["changed_when"] is False
    cf = next(t for t in play["tasks"] if t["name"].startswith("Validate Cloudflare token"))
    assert cf["no_log"] is True
    uri = cf["ansible.builtin.uri"]
    assert uri["method"] == "GET"
    assert uri["url"] == "https://api.cloudflare.com/client/v4/user/tokens/verify"
    assert uri["validate_certs"] is True
    assert uri["follow_redirects"] == "none"
    assert "Bearer {{ DH_CLOUDFLARE_API_TOKEN }}" in uri["headers"]["Authorization"]
    assert cf["changed_when"] is False
    assert "DH_CLOUDFLARE_API_TOKEN" not in str(uri["url"])


def test_missing_optional_credentials_are_not_false_ready():
    txt = PLAYBOOK.read_text()
    assert "'NOT_CONFIGURED'" in txt
    assert "'INCOMPLETE'" in txt
    assert "dh_mqtt_status in ['READY', 'NOT_CONFIGURED']" in txt
    assert "dh_cloudflare_status in ['READY', 'NOT_CONFIGURED']" in txt
    assert "cloudflare_token_scope: NOT_VERIFIED" in txt
    assert "password_or_ticket_logged: false" in txt


def test_mqtt_helper_wont_emit_secret_or_persist_values():
    txt = SCRIPT.read_text()
    assert "sys.stdin.read(" in txt
    assert "print(status)" in txt
    assert "print(host)" not in txt
    assert "print(password)" not in txt
    assert "open(" not in txt
    assert "write_text(" not in txt
    assert "MQTT_PUBLISH" not in txt
    assert "MQTT_SUBSCRIBE" not in txt
