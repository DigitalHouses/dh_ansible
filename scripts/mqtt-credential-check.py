#!/usr/bin/env python3
"""Read-only MQTT 3.1.1 CONNECT/CONNACK credential check.

Input: one JSON object on stdin, with host/username/password. Never put
credentials in argv, a file or stdout. Never publish/subscribe to topics.
The only output is one fixed status token. Port: 1883 (site-local broker).
"""
from __future__ import annotations

import json
import secrets
import socket
import sys


def encoded_string(value: str) -> bytes:
    if not isinstance(value, str):
        raise ValueError("INVALID_INPUT")
    data = value.encode("utf-8")
    if len(data) > 65535:
        raise ValueError("INVALID_INPUT")
    return len(data).to_bytes(2, "big") + data


def remaining_length(value: int) -> bytes:
    if not 0 <= value < 128 * 128 * 128 * 128:
        raise ValueError("INVALID_INPUT")
    result = bytearray()
    while True:
        digit = value % 128
        value //= 128
        if value:
            digit |= 0x80
        result.append(digit)
        if not value:
            return bytes(result)


def receive_exact(sock: socket.socket, count: int) -> bytes:
    data = bytearray()
    while len(data) < count:
        chunk = sock.recv(count - len(data))
        if not chunk:
            raise OSError("MQTT_SERVER_DISCONNECTED")
        data.extend(chunk)
    return bytes(data)


def check(host: str, username: str, password: str) -> str:
    if (not isinstance(host, str) or not 1 <= len(host) <= 253
            or not host[0].isascii() or not host[0].isalnum()
            or not all(c.isascii() and (c.isalnum() or c in ".-")
                       for c in host)
            or ".." in host or host.endswith((".", "-"))):
        return "INVALID_BROKER_HOST"
    try:
        client_id = "dh06-" + secrets.token_hex(8)
        payload = (encoded_string(client_id)
                   + encoded_string(username)
                   + encoded_string(password))
    except (ValueError, UnicodeError):
        return "INVALID_INPUT"
    # Protocol Name MQTT, level 4 = v3.1.1; flags 0xC2:
    # clean session, username and password (no will/publish/subscribe).
    variable_header = encoded_string("MQTT") + bytes((4, 0xC2, 0, 10))
    frame = (b"\x10" + remaining_length(len(variable_header) + len(payload))
             + variable_header + payload)
    try:
        with socket.create_connection((host, 1883), timeout=6) as sock:
            sock.settimeout(6)
            sock.sendall(frame)
            reply = receive_exact(sock, 4)
    except (OSError, TimeoutError, UnicodeError, ValueError):
        return "UNREACHABLE"
    if reply[:3] != b"\x20\x02\x00":
        return "PROTOCOL_ERROR"
    code = reply[3]
    if code == 0:
        return "READY"
    if code in (4, 5):
        return "AUTH_FAILED"
    if code == 3:
        return "UNREACHABLE"
    return "PROTOCOL_ERROR"


def main() -> None:
    try:
        payload = sys.stdin.read(160000)
        if len(payload) >= 160000:
            raise ValueError("OVERSIZED_INPUT")
        values = json.loads(payload)
        if not isinstance(values, dict):
            raise ValueError("INVALID_INPUT")
        status = check(values.get("host"), values.get("username"),
                       values.get("password"))
    except (ValueError, TypeError, UnicodeError):
        status = "INVALID_INPUT"
    print(status)


if __name__ == "__main__":
    main()
