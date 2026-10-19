#!/usr/bin/python
# -*- coding: utf-8 -*-

from __future__ import annotations

DOCUMENTATION = r"""
---
module: truenas_cloudsync_observe
short_description: Read sanitized TrueNAS Cloud Sync state
description:
  - Reads Cloud Sync credentials and tasks through the pinned TrueNAS JSON-RPC API.
  - Returns only non-secret credential metadata and managed task fields.
  - Performs no writes.
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
  timeout:
    type: int
    default: 15
  directory_probes:
    type: list
    elements: dict
    default: []
author:
  - DigitalHouses
"""

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
    return value.replace(":", "").replace(" ", "").upper()


def colonize(value):
    value = normalize_fingerprint(value)
    return ":".join(value[i:i + 2] for i in range(0, len(value), 2))


def tls_peer_fingerprint(host, port, timeout):
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    with socket.create_connection((host, port), timeout=timeout) as raw:
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
            f"wss://{self.host}:{self.port}{self.endpoint}",
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
            result = client.call("auth.login_ex", [{
                "mechanism": "API_KEY_PLAIN",
                "username": username,
                "api_key": api_key,
                "login_options": {"user_info": False},
            }])
        except RpcError as exc:
            if is_auth_rate_limit(exc) and attempt + 1 < attempts:
                time.sleep(delay)
                delay = min(delay * 2.0, 8.0)
                continue
            raise
        return result
    return None


def sanitize_credentials(items):
    result = []
    for item in items:
        provider = item.get("provider") if isinstance(item.get("provider"), dict) else {}
        result.append({
            "id": item.get("id"),
            "name": item.get("name"),
            "provider_type": provider.get("type"),
        })
    return result


def sanitize_tasks(items):
    result = []
    for item in items:
        credentials = item.get("credentials") if isinstance(item.get("credentials"), dict) else {}
        provider = credentials.get("provider") if isinstance(credentials.get("provider"), dict) else {}
        attributes = item.get("attributes") if isinstance(item.get("attributes"), dict) else {}
        schedule = item.get("schedule") if isinstance(item.get("schedule"), dict) else {}
        result.append({
            "id": item.get("id"),
            "description": item.get("description"),
            "path": item.get("path"),
            "credential_id": credentials.get("id"),
            "credential_name": credentials.get("name"),
            "credential_provider_type": provider.get("type"),
            "attributes": {
                "bucket": attributes.get("bucket"),
                "folder": attributes.get("folder"),
                "fast_list": bool(attributes.get("fast_list", False)),
            },
            "schedule": {
                "minute": str(schedule.get("minute", "00")),
                "hour": str(schedule.get("hour", "*")),
                "dom": str(schedule.get("dom", "*")),
                "month": str(schedule.get("month", "*")),
                "dow": str(schedule.get("dow", "*")),
            },
            "pre_script_present": bool(str(item.get("pre_script") or "").strip()),
            "post_script_present": bool(str(item.get("post_script") or "").strip()),
            "args_present": bool(str(item.get("args") or "").strip()),
            "locked": bool(item.get("locked", False)),
            "snapshot": bool(item.get("snapshot", False)),
            "include": item.get("include") or [],
            "exclude": item.get("exclude") or [],
            "enabled": bool(item.get("enabled", False)),
            "bwlimit": item.get("bwlimit") or [],
            "transfers": item.get("transfers"),
            "direction": item.get("direction"),
            "transfer_mode": item.get("transfer_mode"),
            "encryption": bool(item.get("encryption", False)),
            "filename_encryption": bool(item.get("filename_encryption", False)),
            "create_empty_src_dirs": bool(item.get("create_empty_src_dirs", False)),
            "follow_symlinks": bool(item.get("follow_symlinks", False)),
        })
    return result


def sanitize_directory_entries(items):
    result = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        result.append({
            "name": item.get("Name") if "Name" in item else item.get("name"),
            "is_dir": bool(item.get("IsDir") if "IsDir" in item else item.get("is_dir")),
            "size": item.get("Size") if "Size" in item else item.get("size"),
        })
    return result


def main():
    module = AnsibleModule(
        argument_spec=dict(
            host=dict(type="str", required=True),
            port=dict(type="int", default=443),
            endpoint=dict(type="str", default="/api/current"),
            tls_sha256_fingerprint=dict(type="str", required=True),
            username=dict(type="str", required=True),
            api_key=dict(type="str", required=True, no_log=True),
            timeout=dict(type="int", default=15),
            directory_probes=dict(type="list", elements="dict", default=[]),
        ),
        supports_check_mode=True,
    )

    if connect is None:
        module.fail_json(msg="Python dependency 'websockets' is required on the Ansible runner.")

    expected = normalize_fingerprint(module.params["tls_sha256_fingerprint"])
    try:
        actual = tls_peer_fingerprint(module.params["host"], module.params["port"], module.params["timeout"])
    except Exception as exc:
        module.fail_json(msg=f"Unable to read TrueNAS TLS certificate: {type(exc).__name__}: {exc}")

    if actual != expected:
        module.fail_json(
            msg="TrueNAS TLS fingerprint mismatch. Authentication was not attempted.",
            expected_tls_sha256=colonize(expected),
            actual_tls_sha256=colonize(actual),
        )

    try:
        with Client(
            module.params["host"],
            module.params["port"],
            module.params["endpoint"],
            module.params["timeout"],
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

            credentials = client.call("cloudsync.credentials.query", [[], {}])
            tasks = client.call("cloudsync.query", [[], {}])

            directory_probe_results = []
            by_name = {
                item.get("name"): item
                for item in credentials
                if isinstance(item, dict) and item.get("name")
            }
            for probe in module.params["directory_probes"]:
                label = str(probe.get("label") or "")
                credential_name = str(probe.get("credential_name") or "")
                bucket = str(probe.get("bucket") or "")
                folder = str(probe.get("folder") or "")
                credential_item = by_name.get(credential_name)
                if not credential_item:
                    directory_probe_results.append({
                        "label": label,
                        "bucket": bucket,
                        "folder": folder,
                        "ok": False,
                        "error": "credential_not_found",
                        "entries": [],
                    })
                    continue
                try:
                    listing = client.call(
                        "cloudsync.list_directory",
                        [{
                            "credentials": credential_item.get("id"),
                            "encryption": False,
                            "filename_encryption": False,
                            "encryption_password": "",
                            "encryption_salt": "",
                            "attributes": {
                                "bucket": bucket,
                                "folder": folder,
                                "fast_list": False,
                            },
                        }],
                    )
                    directory_probe_results.append({
                        "label": label,
                        "bucket": bucket,
                        "folder": folder,
                        "ok": True,
                        "error": None,
                        "entries": sanitize_directory_entries(listing),
                    })
                except RpcError as exc:
                    safe = safe_rpc_error(exc)
                    directory_probe_results.append({
                        "label": label,
                        "bucket": bucket,
                        "folder": folder,
                        "ok": False,
                        "error": safe,
                        "entries": [],
                    })

    except RpcError as exc:
        module.fail_json(msg="TrueNAS Cloud Sync read failed.", rpc_error=safe_rpc_error(exc))
    except Exception as exc:
        module.fail_json(msg=f"TrueNAS Cloud Sync API failure: {type(exc).__name__}: {exc}")

    if not isinstance(credentials, list) or not isinstance(tasks, list):
        module.fail_json(msg="TrueNAS Cloud Sync query returned an unexpected shape.")

    module.exit_json(
        changed=False,
        result="PASS",
        credentials=sanitize_credentials(credentials),
        tasks=sanitize_tasks(tasks),
        directory_probes=directory_probe_results,
        tls_sha256_fingerprint=colonize(actual),
    )


if __name__ == "__main__":
    main()
