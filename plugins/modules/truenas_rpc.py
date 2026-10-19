#!/usr/bin/python
# -*- coding: utf-8 -*-

from __future__ import annotations

DOCUMENTATION = r'''
---
module: truenas_rpc
short_description: Call TrueNAS JSON-RPC methods over a pinned TLS WebSocket
description:
  - Connects to the TrueNAS JSON-RPC 2.0 WebSocket API.
  - Verifies the server certificate SHA-256 fingerprint before sending credentials.
  - Authenticates with auth.login_ex using a user-linked API key.
  - Executes a batch of API calls over one persistent authenticated connection.
options:
  host:
    type: str
    required: true
  port:
    type: int
    default: 443
  endpoint:
    type: str
    default: /api/current
  tls_sha256_fingerprint:
    type: str
    required: true
  username:
    type: str
    required: true
  api_key:
    type: str
    required: true
    no_log: true
  calls:
    type: list
    elements: dict
    required: true
    suboptions:
      name:
        type: str
        required: true
      method:
        type: str
        required: true
      params:
        type: list
        default: []
  timeout:
    type: int
    default: 10
author:
  - DigitalHouses
'''

EXAMPLES = r'''
- name: Read TrueNAS system and pools
  truenas_rpc:
    host: 198.51.100.20
    tls_sha256_fingerprint: "AA:BB:CC:..."
    username: ansimble-audit
    api_key: "{{ vault_truenas_audit_api_key }}"
    calls:
      - name: system
        method: system.info
      - name: pools
        method: pool.query
        params: [[], {}]
'''

RETURN = r'''
results:
  description: Mapping of call names to TrueNAS method results.
  returned: success
  type: dict
tls_sha256_fingerprint:
  description: Normalized server certificate SHA-256 fingerprint.
  returned: success
  type: str
'''

import hashlib
import json
import socket
import ssl
import time

from ansible.module_utils.basic import AnsibleModule

try:
    from websockets.sync.client import connect
except ImportError:
    connect = None


def normalize_fingerprint(value: str) -> str:
    return value.replace(":", "").replace(" ", "").upper()


def fingerprint_colonized(value: str) -> str:
    value = normalize_fingerprint(value)
    return ":".join(value[i:i + 2] for i in range(0, len(value), 2))


def tls_peer_fingerprint(host: str, port: int, timeout: int) -> str:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE

    with socket.create_connection((host, port), timeout=timeout) as raw:
        with context.wrap_socket(raw, server_hostname=host) as tls:
            cert = tls.getpeercert(binary_form=True)

    return hashlib.sha256(cert).hexdigest().upper()


class RpcError(Exception):
    def __init__(self, method: str, payload: dict):
        self.method = method
        self.payload = payload
        super().__init__(method)


class TrueNasRpcClient:
    def __init__(self, host, port, endpoint, timeout):
        self.host = host
        self.port = port
        self.endpoint = endpoint
        self.timeout = timeout
        self.next_id = 1
        self.ws = None

    def __enter__(self):
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        uri = f"wss://{self.host}:{self.port}{self.endpoint}"
        self.ws = connect(
            uri,
            ssl=context,
            open_timeout=self.timeout,
            close_timeout=self.timeout,
        )
        return self

    def __exit__(self, exc_type, exc, tb):
        if self.ws is not None:
            self.ws.close()

    def call(self, method, params=None):
        request_id = self.next_id
        self.next_id += 1
        request = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params or [],
        }
        self.ws.send(json.dumps(request))

        while True:
            raw = self.ws.recv(timeout=self.timeout)
            response = json.loads(raw)
            if response.get("id") != request_id:
                continue
            if "error" in response:
                raise RpcError(method, response["error"])
            return response.get("result")


def safe_rpc_error(error: RpcError) -> dict:
    payload = error.payload if isinstance(error.payload, dict) else {}
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    return {
        "method": error.method,
        "code": payload.get("code"),
        "message": payload.get("message"),
        "errname": data.get("errname"),
        "reason": data.get("reason"),
    }


def is_auth_rate_limit(error: RpcError) -> bool:
    if error.method != "auth.login_ex":
        return False
    safe = safe_rpc_error(error)
    return (
        safe.get("errname") == "EBUSY"
        or "rate limit" in str(safe.get("reason") or "").lower()
        or "rate limit" in str(safe.get("message") or "").lower()
    )


def login_with_retry(client, username, api_key, attempts=6):
    delay = 1.0
    for attempt in range(attempts):
        try:
            auth = client.call(
                "auth.login_ex",
                [{
                    "mechanism": "API_KEY_PLAIN",
                    "username": username,
                    "api_key": api_key,
                    "login_options": {"user_info": False},
                }],
            )
        except RpcError as exc:
            if is_auth_rate_limit(exc) and attempt + 1 < attempts:
                time.sleep(delay)
                delay = min(delay * 2.0, 8.0)
                continue
            raise
        return auth
    return None


def main():
    module = AnsibleModule(
        argument_spec=dict(
            host=dict(type="str", required=True),
            port=dict(type="int", default=443),
            endpoint=dict(type="str", default="/api/current"),
            tls_sha256_fingerprint=dict(type="str", required=True),
            username=dict(type="str", required=True),
            api_key=dict(type="str", required=True, no_log=True),
            calls=dict(
                type="list",
                elements="dict",
                required=True,
                options=dict(
                    name=dict(type="str", required=True),
                    method=dict(type="str", required=True),
                    params=dict(type="list", default=[]),
                ),
            ),
            timeout=dict(type="int", default=10),
        ),
        supports_check_mode=True,
    )

    if connect is None:
        module.fail_json(
            msg="Python dependency 'websockets' is required on the Ansible runner."
        )

    host = module.params["host"]
    port = module.params["port"]
    timeout = module.params["timeout"]
    expected = normalize_fingerprint(module.params["tls_sha256_fingerprint"])

    try:
        actual = tls_peer_fingerprint(host, port, timeout)
    except Exception as exc:
        module.fail_json(msg=f"Unable to read TrueNAS TLS certificate: {type(exc).__name__}: {exc}")

    if actual != expected:
        module.fail_json(
            msg="TrueNAS TLS fingerprint mismatch. Authentication was not attempted.",
            expected_tls_sha256=fingerprint_colonized(expected),
            actual_tls_sha256=fingerprint_colonized(actual),
        )

    calls = module.params["calls"]
    allowed_exact = {
        "system.info",
        "filesystem.stat",
        "filesystem.getacl",
        "filesystem.listdir",
        "pool.snapshot.query",
        "nfs.config",
        "smb.config",
        "user.has_local_administrator_set_up",
    }
    unsafe = [item["method"] for item in calls if not (item["method"].endswith(".query") or item["method"] in allowed_exact)]
    if unsafe:
        module.fail_json(msg="truenas_rpc audit transport rejected non-read-only methods.", rejected_methods=unsafe)

    names = [item["name"] for item in calls]
    if len(names) != len(set(names)):
        module.fail_json(msg="TrueNAS RPC call names must be unique.")

    try:
        with TrueNasRpcClient(
            host,
            port,
            module.params["endpoint"],
            timeout,
        ) as client:
            auth = login_with_retry(
                client,
                module.params["username"],
                module.params["api_key"],
            )
            if not isinstance(auth, dict) or auth.get("response_type") != "SUCCESS":
                module.fail_json(
                    msg="TrueNAS API authentication did not succeed.",
                    response_type=auth.get("response_type") if isinstance(auth, dict) else None,
                )

            results = {}
            for item in calls:
                results[item["name"]] = client.call(item["method"], item["params"])

    except RpcError as exc:
        module.fail_json(msg="TrueNAS JSON-RPC method failed.", rpc_error=safe_rpc_error(exc))
    except Exception as exc:
        module.fail_json(msg=f"TrueNAS WebSocket API failure: {type(exc).__name__}: {exc}")

    module.exit_json(
        changed=False,
        results=results,
        tls_sha256_fingerprint=fingerprint_colonized(actual),
    )


if __name__ == "__main__":
    main()
