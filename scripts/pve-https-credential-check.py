#!/usr/bin/env python3
"""PVE HTTPS credential check for legacy PVE 8 CA certificates.

Input: one JSON object via stdin (including password). Never accept password
from argv/env, log HTTP bodies, disable hostname verification, or trust an
unverified TLS session. Output: a single public readiness status, no secrets.

OpenSSL 3/Python 3.13 default VERIFY_X509_STRICT rejects some PVE 8 cluster
CAs without keyUsage. Scope the single compatibility flag to this connection.
"""
import hashlib
import http.client
import ipaddress
import json
import socket
import ssl
import sys
import urllib.parse

STATES = frozenset({
    "READY", "AUTH_FAILED", "INSUFFICIENT_PRIVILEGES", "PVE_NODE_MISMATCH",
    "PVE_TLS_IDENTITY_MISMATCH", "PVE_TLS_CERT_UNTRUSTED",
    "UNREACHABLE", "PVE_TLS_OR_API_UNAVAILABLE",
})


class IdentityMismatch(Exception):
    pass


def read_config():
    raw = json.load(sys.stdin)
    if not isinstance(raw, dict):
        raise ValueError("Expected JSON object")
    ip = str(ipaddress.IPv4Address(raw["ip"]))
    hostname = raw.get("api_hostname") or ip
    if not isinstance(hostname, str) or not 1 <= len(hostname) <= 253:
        raise ValueError("Invalid API hostname")
    if any(c in hostname for c in "/?#@:\\"):
        raise ValueError("Invalid API hostname characters")
    port = int(raw.get("port", 8006))
    if not 1 <= port <= 65535:
        raise ValueError("Invalid API port")
    expected_fp = raw["fingerprint"].upper()
    if len(expected_fp) != 95 or any(
        c not in "0123456789ABCDEF:" for c in expected_fp
    ):
        raise ValueError("Invalid TLS fingerprint")
    ca_file = raw["ca_file"]
    node = raw["node"]
    secret = raw["password"]
    if not isinstance(ca_file, str) or not isinstance(node, str):
        raise ValueError("Invalid trust metadata")
    if not isinstance(secret, str) or not secret:
        raise ValueError("Password missing")
    return ip, hostname, port, ca_file, expected_fp, node, secret


def make_context(ca_file):
    ctx = ssl.create_default_context(cafile=ca_file)
    # This is NOT validate_certs:false. Keep CERT_REQUIRED and check_hostname.
    if hasattr(ssl, "VERIFY_X509_STRICT"):
        ctx.verify_flags &= ~ssl.VERIFY_X509_STRICT
    if ctx.verify_mode != ssl.CERT_REQUIRED or not ctx.check_hostname:
        raise ValueError("TLS validation must remain enabled")
    return ctx


def send_verified(ip, hostname, port, ctx, fingerprint, method, route,
                  body=None, headers=None):
    # Connect to actual observed PVE IP, validate certificate for hostname.
    # Each request performs full TLS verification AND pin check before sending
    # any credential or authentication cookie (including on reconnection).
    conn = http.client.HTTPSConnection(hostname, port, context=ctx, timeout=12)
    conn._create_connection = lambda address, timeout=None, source_address=None: (
        socket.create_connection((ip, port), timeout=12))
    try:
        conn.connect()
        cert = conn.sock.getpeercert(binary_form=True)
        digest = hashlib.sha256(cert).hexdigest().upper()
        observed = ":".join(digest[i:i + 2] for i in range(0, 64, 2))
        if observed != fingerprint:
            raise IdentityMismatch()
        conn.request(method, route, body=body, headers=headers or {})
        response = conn.getresponse()
        data = response.read(1_000_001)
        if len(data) > 1_000_000:
            return response.status, None
        return response.status, data
    finally:
        conn.close()


def check():
    ip, host, port, ca, fingerprint, node, secret = read_config()
    ctx = make_context(ca)
    body = urllib.parse.urlencode({
        "username": "root@pam", "password": secret,
    }).encode("utf-8")
    status, data = send_verified(
        ip, host, port, ctx, fingerprint, "POST",
        "/api2/json/access/ticket", body,
        {"Content-Type": "application/x-www-form-urlencoded"},
    )
    if status in (401, 403):
        return "AUTH_FAILED"
    if status != 200 or data is None:
        return "PVE_TLS_OR_API_UNAVAILABLE"
    try:
        ticket = json.loads(data)["data"]["ticket"]
        if not isinstance(ticket, str) or len(ticket) < 10:
            return "PVE_TLS_OR_API_UNAVAILABLE"
    except (ValueError, KeyError, TypeError):
        return "PVE_TLS_OR_API_UNAVAILABLE"
    status, data = send_verified(
        ip, host, port, ctx, fingerprint, "GET",
        "/api2/json/nodes", None,
        {"Cookie": "PVEAuthCookie=" + ticket},
    )
    if status in (401, 403):
        return "INSUFFICIENT_PRIVILEGES"
    if status != 200 or data is None:
        return "PVE_TLS_OR_API_UNAVAILABLE"
    try:
        nodes = json.loads(data)["data"]
        if not isinstance(nodes, list):
            return "PVE_TLS_OR_API_UNAVAILABLE"
        return ("READY" if node in [obj["node"] for obj in nodes]
                else "PVE_NODE_MISMATCH")
    except (ValueError, KeyError, TypeError):
        return "PVE_TLS_OR_API_UNAVAILABLE"


def main():
    try:
        state = check()
    except IdentityMismatch:
        state = "PVE_TLS_IDENTITY_MISMATCH"
    except ssl.SSLCertVerificationError:
        state = "PVE_TLS_CERT_UNTRUSTED"
    except (socket.timeout, TimeoutError, ConnectionError, OSError):
        state = "UNREACHABLE"
    except (ValueError, KeyError, TypeError, ssl.SSLError, http.client.HTTPException):
        state = "PVE_TLS_OR_API_UNAVAILABLE"
    if state not in STATES:
        state = "PVE_TLS_OR_API_UNAVAILABLE"
    print(state)


if __name__ == "__main__":
    main()
