#!/usr/bin/python
# -*- coding: utf-8 -*-

from __future__ import annotations

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


def normalize_fingerprint(value):
    return str(value or "").replace(":", "").replace(" ", "").upper()


def colonize(value):
    value = normalize_fingerprint(value)
    return ":".join(value[i:i + 2] for i in range(0, len(value), 2))


def tls_peer_fingerprint(host, port, timeout):
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    with socket.create_connection((host, port), timeout=min(timeout, 20)) as raw:
        with context.wrap_socket(raw, server_hostname=host) as tls:
            cert = tls.getpeercert(binary_form=True)
    return hashlib.sha256(cert).hexdigest().upper()


class RpcError(Exception):
    def __init__(self, method, payload):
        self.method = method
        self.payload = payload
        super().__init__(method)


class Client:
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
        self.ws = connect(
            "wss://{}:{}{}".format(self.host, self.port, self.endpoint),
            ssl=context,
            open_timeout=min(self.timeout, 20),
            close_timeout=min(self.timeout, 20),
        )
        return self

    def __exit__(self, exc_type, exc, tb):
        if self.ws is not None:
            self.ws.close()

    def call(self, method, params=None):
        request_id = self.next_id
        self.next_id += 1
        self.ws.send(json.dumps({
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params or [],
        }))
        while True:
            response = json.loads(self.ws.recv(timeout=self.timeout))
            if response.get("id") != request_id:
                continue
            if "error" in response:
                raise RpcError(method, response["error"])
            return response.get("result")


def safe_rpc_error(error):
    payload = error.payload if isinstance(error.payload, dict) else {}
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    return {
        "method": error.method,
        "code": payload.get("code"),
        "message": payload.get("message"),
        "errname": data.get("errname"),
        "reason": data.get("reason"),
    }


def is_auth_rate_limit(error):
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
            result = client.call(
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

        return isinstance(result, dict) and result.get("response_type") == "SUCCESS"
    return False


def query(client, method):
    result = client.call(method, [[], {}])
    return result if isinstance(result, list) else []


def main():
    module = AnsibleModule(
        argument_spec=dict(
            host=dict(type="str", required=True),
            port=dict(type="int", default=443),
            endpoint=dict(type="str", default="/api/current"),
            tls_sha256_fingerprint=dict(type="str", required=True),
            audit_username=dict(type="str", required=True),
            audit_api_key=dict(type="str", required=True, no_log=True),
            filesystem_username=dict(type="str", required=True),
            filesystem_api_key=dict(type="str", required=True, no_log=True),
            paths=dict(type="list", elements="str", required=True),
            timeout=dict(type="int", default=30),
        ),
        supports_check_mode=True,
    )

    if connect is None:
        module.fail_json(msg="Python dependency 'websockets' is required.")

    paths = list(module.params["paths"])
    if len(paths) != len(set(paths)):
        module.fail_json(msg="TrueNAS access observation paths must be unique.")
    if any(not path.startswith("/mnt/") for path in paths):
        module.fail_json(msg="TrueNAS access observation only accepts absolute /mnt paths.")

    expected = normalize_fingerprint(module.params["tls_sha256_fingerprint"])
    try:
        actual = tls_peer_fingerprint(
            module.params["host"],
            module.params["port"],
            module.params["timeout"],
        )
    except Exception as exc:
        module.fail_json(msg="Unable to read TrueNAS TLS certificate: {}: {}".format(type(exc).__name__, exc))

    if actual != expected:
        module.fail_json(
            msg="TrueNAS TLS fingerprint mismatch. Authentication was not attempted.",
            expected_tls_sha256=colonize(expected),
            actual_tls_sha256=colonize(actual),
        )

    try:
        # Session 1: policy-safe audit identity for control-plane reads.
        with Client(
            module.params["host"],
            module.params["port"],
            module.params["endpoint"],
            module.params["timeout"],
        ) as client:
            if not login_with_retry(
                client,
                module.params["audit_username"],
                module.params["audit_api_key"],
            ):
                module.fail_json(msg="TrueNAS audit API authentication failed.")

            smb_shares = query(client, "sharing.smb.query")
            nfs_shares = query(client, "sharing.nfs.query")
            services = query(client, "service.query")
            nfs_config = client.call("nfs.config")
            smb_config = client.call("smb.config")

        # Session 2: FULL_ADMIN identity, but this module contains no mutating
        # RPC methods. It is used only because hardened filesystem ACLs may
        # prevent the READONLY_ADMIN identity from listing protected paths.
        acls = []
        directory_counts = {}
        with Client(
            module.params["host"],
            module.params["port"],
            module.params["endpoint"],
            module.params["timeout"],
        ) as client:
            if not login_with_retry(
                client,
                module.params["filesystem_username"],
                module.params["filesystem_api_key"],
            ):
                module.fail_json(msg="TrueNAS filesystem-read API authentication failed.")

            for path in paths:
                acl = client.call("filesystem.getacl", [path, False, True])
                entries = client.call(
                    "filesystem.listdir",
                    [path, [], {"select": ["name", "type"]}],
                ) or []
                acls.append({
                    "path": path,
                    "user": acl.get("user"),
                    "group": acl.get("group"),
                    "uid": acl.get("uid"),
                    "gid": acl.get("gid"),
                    "acltype": acl.get("acltype"),
                    "trivial": acl.get("trivial"),
                    "acl": acl.get("acl", []),
                })
                directory_counts[path] = len(entries)

    except RpcError as exc:
        module.fail_json(
            msg="TrueNAS access observation JSON-RPC method failed.",
            rpc_error=safe_rpc_error(exc),
        )
    except Exception as exc:
        module.fail_json(
            msg="TrueNAS access observation failed: {}: {}".format(type(exc).__name__, exc)
        )

    module.exit_json(
        changed=False,
        result="PASS",
        tls_sha256_fingerprint=colonize(actual),
        acls=acls,
        directory_counts=directory_counts,
        smb_shares=smb_shares,
        nfs_shares=nfs_shares,
        services=services,
        nfs_config=nfs_config,
        smb_config=smb_config,
    )


if __name__ == "__main__":
    main()
