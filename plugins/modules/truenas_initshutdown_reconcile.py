#!/usr/bin/python
from __future__ import annotations

DOCUMENTATION = r"""
---
module: truenas_initshutdown_reconcile
short_description: Audit or reconcile one managed TrueNAS init/shutdown command
options:
  host: {type: str, required: true}
  port: {type: int, default: 443}
  endpoint: {type: str, default: /api/current}
  tls_sha256_fingerprint: {type: str, required: true}
  username: {type: str, required: true}
  api_key: {type: str, required: true, no_log: true}
  desired:
    type: dict
    required: true
  apply:
    type: bool
    default: false
  sync_catalog_if_needed:
    type: bool
    default: false
  catalog_sync_timeout:
    type: int
    default: 600
  timeout:
    type: int
    default: 20
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
    return str(value).replace(":", "").replace(" ", "").upper()


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
            return client.call("auth.login_ex", [{
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
    return None


def canonical(item):
    return {
        "type": str(item.get("type") or ""),
        "command": str(item.get("command") or ""),
        "script": str(item.get("script") or ""),
        "when": str(item.get("when") or ""),
        "enabled": bool(item.get("enabled", False)),
        "timeout": int(item.get("timeout") or 0),
        "comment": str(item.get("comment") or ""),
    }


def query(client, method, filters=None, options=None):
    result = client.call(method, [filters or [], options or {}])
    return result if isinstance(result, list) else []


def wait_job(client, job_id, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        jobs = query(client, "core.get_jobs", [["id", "=", int(job_id)]], {})
        if jobs:
            state = str(jobs[0].get("state") or "").upper()
            if state == "SUCCESS":
                return
            if state in {"FAILED", "ABORTED"}:
                raise RuntimeError(
                    "TrueNAS catalog sync job {} ended in {}: {}".format(
                        job_id,
                        state,
                        jobs[0].get("error") or jobs[0].get("exception") or "unknown error",
                    )
                )
        time.sleep(0.5)
    raise TimeoutError("Timed out waiting for TrueNAS catalog sync job.")


def desired_payload(raw):
    return {
        "type": str(raw["type"]),
        "command": str(raw.get("command") or ""),
        "script": str(raw.get("script") or ""),
        "when": str(raw["when"]),
        "enabled": bool(raw.get("enabled", True)),
        "timeout": int(raw.get("timeout", 10)),
        "comment": str(raw["comment"]),
    }


def main():
    module = AnsibleModule(
        argument_spec=dict(
            host=dict(type="str", required=True),
            port=dict(type="int", default=443),
            endpoint=dict(type="str", default="/api/current"),
            tls_sha256_fingerprint=dict(type="str", required=True),
            username=dict(type="str", required=True),
            api_key=dict(type="str", required=True, no_log=True),
            desired=dict(type="dict", required=True),
            apply=dict(type="bool", default=False),
            sync_catalog_if_needed=dict(type="bool", default=False),
            catalog_sync_timeout=dict(type="int", default=600),
            timeout=dict(type="int", default=20),
        ),
        supports_check_mode=True,
    )

    if connect is None:
        module.fail_json(msg="Python dependency 'websockets' is required on the Ansible runner.")

    desired = desired_payload(module.params["desired"])
    if desired["type"] != "COMMAND":
        module.fail_json(msg="Managed TrueNAS runtime policy must use COMMAND.")
    if desired["when"] != "PREINIT":
        module.fail_json(msg="Managed TrueNAS runtime policy must use PREINIT.")
    if not desired["command"].strip():
        module.fail_json(msg="Managed TrueNAS runtime command must not be empty.")
    if not desired["comment"].strip():
        module.fail_json(msg="Managed TrueNAS runtime comment must not be empty.")

    expected_fp = normalize_fingerprint(module.params["tls_sha256_fingerprint"])
    try:
        actual_fp = tls_peer_fingerprint(
            module.params["host"], module.params["port"], module.params["timeout"]
        )
    except Exception as exc:
        module.fail_json(msg=f"TrueNAS TLS probe failed: {type(exc).__name__}: {exc}")

    if actual_fp != expected_fp:
        module.fail_json(
            msg="TrueNAS TLS fingerprint mismatch.",
            expected=expected_fp,
            observed=actual_fp,
        )

    changed = False
    try:
        with Client(
            module.params["host"],
            module.params["port"],
            module.params["endpoint"],
            module.params["timeout"],
        ) as client:
            auth = login_with_retry(client, module.params["username"], module.params["api_key"])
            if not isinstance(auth, dict) or auth.get("response_type") != "SUCCESS":
                module.fail_json(msg="TrueNAS API authentication did not succeed.")

            catalog_synced = bool(client.call("catalog.synced", []))
            catalog_sync_triggered = False
            if (
                not catalog_synced
                and module.params["sync_catalog_if_needed"]
                and not module.check_mode
            ):
                job_id = client.call("catalog.sync", [])
                if not isinstance(job_id, int) or isinstance(job_id, bool):
                    raise RuntimeError("TrueNAS catalog.sync did not return a job id.")
                wait_job(client, job_id, module.params["catalog_sync_timeout"])
                catalog_synced = bool(client.call("catalog.synced", []))
                catalog_sync_triggered = True
                changed = True
                if not catalog_synced:
                    raise RuntimeError("TrueNAS catalog.sync completed but catalog.synced is still false.")

            items = client.call("initshutdownscript.query", [[], {}]) or []
            matches = [
                item for item in items
                if isinstance(item, dict) and str(item.get("comment") or "") == desired["comment"]
            ]
            if len(matches) > 1:
                module.fail_json(
                    msg="Duplicate managed TrueNAS init/shutdown policies found.",
                    count=len(matches),
                    comment=desired["comment"],
                )

            observed = canonical(matches[0]) if matches else None
            observed_id = matches[0].get("id") if matches else None

            if observed is None:
                action = "CREATE"
            elif observed == desired:
                action = "NOOP"
            else:
                action = "UPDATE"

            do_apply = bool(module.params["apply"]) and not module.check_mode
            if do_apply and action == "CREATE":
                client.call("initshutdownscript.create", [desired])
                changed = True
            elif do_apply and action == "UPDATE":
                client.call("initshutdownscript.update", [int(observed_id), desired])
                changed = True

            if do_apply and action in ("CREATE", "UPDATE"):
                items = client.call("initshutdownscript.query", [[], {}]) or []
                matches = [
                    item for item in items
                    if isinstance(item, dict)
                    and str(item.get("comment") or "") == desired["comment"]
                ]
                if len(matches) != 1 or canonical(matches[0]) != desired:
                    module.fail_json(
                        msg="Managed TrueNAS init/shutdown policy did not converge exactly."
                    )
                observed = canonical(matches[0])
                action = "NOOP"

    except RpcError as exc:
        module.fail_json(
            msg="TrueNAS runtime policy JSON-RPC method failed.",
            rpc_error=safe_rpc_error(exc),
        )
    except Exception as exc:
        module.fail_json(msg=f"TrueNAS runtime policy failure: {type(exc).__name__}: {exc}")

    module.exit_json(
        changed=changed,
        result="PASS",
        action=action,
        desired=desired,
        observed=observed,
        catalog_synced=catalog_synced,
        catalog_sync_triggered=catalog_sync_triggered,
        secret_values_reported=False,
    )


if __name__ == "__main__":
    main()
