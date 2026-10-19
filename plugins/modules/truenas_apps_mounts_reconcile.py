#!/usr/bin/python
# -*- coding: utf-8 -*-
"""Pinned, idempotent TrueNAS Apps mounts reconciler. No destructive calls."""
from __future__ import annotations

DOCUMENTATION = """
---
module: truenas_apps_mounts_reconcile
short_description: Safely reconcile Backrest and FileBrowser mounts with pinned TrueNAS API
description:
  - Pinned TLS and authenticated TrueNAS JSON-RPC are mandatory.
  - Only Backrest update and FileBrowser Quantum create are permitted.
  - Never deletes or replaces pools, installed applications, or ixVolumes.
  - Secret-bearing arguments never appear in module output.
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
  app_password:
    type: str
    required: true
    no_log: true
  applications:
    type: list
    elements: dict
    required: true
  pool_name:
    type: str
    required: true
  apply:
    type: bool
    default: false
  timeout:
    type: int
    default: 30
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


class RpcError(Exception):
    def __init__(self, method, data):
        self.method = method
        self.code = data.get("code") if isinstance(data, dict) else None
        super().__init__(method)


class Client:
    def __init__(self, host, port, endpoint, timeout):
        self.uri = f"wss://{host}:{port}{endpoint}"
        self.timeout = timeout
        self.ws = None
        self.sequence = 0

    def __enter__(self):
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        self.ws = connect(self.uri, ssl=context,
                          open_timeout=self.timeout, close_timeout=self.timeout)
        return self

    def __exit__(self, *args):
        if self.ws:
            self.ws.close()

    def call(self, method, args=None):
        self.sequence += 1
        current = self.sequence
        self.ws.send(json.dumps({"jsonrpc": "2.0", "id": current,
                                 "method": method, "params": args or []}))
        while True:
            reply = json.loads(self.ws.recv(timeout=self.timeout))
            if reply.get("id") != current:
                continue
            if reply.get("error"):
                raise RpcError(method, reply["error"])
            return reply.get("result")


def tls_pin(host, port, timeout):
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    with socket.create_connection((host, port), timeout=timeout) as raw:
        with ctx.wrap_socket(raw, server_hostname=host) as channel:
            return hashlib.sha256(channel.getpeercert(binary_form=True)).hexdigest().upper()


def wait_job(client, job_id, timeout=900):
    if not isinstance(job_id, int) or isinstance(job_id, bool):
        raise RuntimeError("Middleware did not return a job id.")
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        jobs = client.call("core.get_jobs", [[["id", "=", job_id]], {}])
        if jobs:
            state = str(jobs[0].get("state", "")).upper()
            if state == "SUCCESS":
                return
            if state in ("FAILED", "ABORTED"):
                raise RuntimeError(f"Middleware {job_id} ended in {state}.")
        time.sleep(2)
    raise RuntimeError("Middleware app job timed out.")


def mounts(values):
    items = values.get("storage", {}).get("additional_storage", []) or []
    return sorted((x["host_path_config"]["path"], x["mount_path"],
                   bool(x["read_only"])) for x in items)


def safe_inventory(module, app, pool):
    name = app.get("name")
    if name not in ("backrest", "filebrowser-quantum"):
        module.fail_json(msg="Apps reconciliation forbids other applications.")
    if app.get("catalog_app") != name or app.get("train") != "community":
        module.fail_json(msg="Catalog identity or train differs from the accepted contract.")
    version = str(app.get("version") or "")
    if not version or version == "latest":
        module.fail_json(msg="Catalog version must be pinned.")
    expected = {
        "backrest": {(f"/mnt/{pool}/docs", "/sources/docs", True)},
        "filebrowser-quantum": {
            (f"/mnt/{pool}/docs", "/data/docs", False),
            (f"/mnt/{pool}/digitalhouses", "/data/digitalhouses", False),
            (f"/mnt/{pool}/data", "/data/data", False),
            (f"/mnt/{pool}/backups", "/data/backups", False),
        },
    }
    if set(mounts(app.get("values", {}))) != expected[name]:
        module.fail_json(msg="App storage mounts violate the site dataset allowlist.")
    user = app.get("values", {}).get("run_as", {})
    required_uid, required_gid = ((568, 3102) if name == "backrest" else (3200, 3102))
    if (user.get("user"), user.get("group")) != (required_uid, required_gid):
        module.fail_json(msg="App run_as identity violates least privilege.")
    network = app.get("values", {}).get("network", {}).get("web_port", {})
    if network.get("bind_mode") != "exposed":
        module.fail_json(msg="Network ports must remain unpublished until Cloudflare Access is ready.")
    if name == "filebrowser-quantum" and not app.get("secret_fields"):
        module.fail_json(msg="FileBrowser must reference the managed admin secret.")


def main():
    module = AnsibleModule(
        argument_spec=dict(
            host=dict(type="str", required=True),
            port=dict(type="int", default=443),
            endpoint=dict(type="str", default="/api/current"),
            tls_sha256_fingerprint=dict(type="str", required=True),
            username=dict(type="str", required=True),
            api_key=dict(type="str", required=True, no_log=True),
            app_password=dict(type="str", required=True, no_log=True),
            applications=dict(type="list", elements="dict", required=True),
            pool_name=dict(type="str", required=True),
            apply=dict(type="bool", default=False),
            timeout=dict(type="int", default=30),
        ), supports_check_mode=True
    )
    a = module.params
    if connect is None:
        module.fail_json(msg="websockets sync dependency missing.")
    if not a["pool_name"].replace("_", "").isalnum():
        module.fail_json(msg="Unsafe pool name.")
    desired = [x for x in a["applications"] if x.get("name") in
               ("backrest", "filebrowser-quantum")]
    if len(desired) != 2 or not a["app_password"] or len(a["app_password"]) < 8:
        module.fail_json(msg="Incomplete Apps secret or exactly two managed targets not found.")
    for app in desired:
        safe_inventory(module, app, a["pool_name"])
    expected = a["tls_sha256_fingerprint"].replace(":", "").upper()
    try:
        if tls_pin(a["host"], a["port"], a["timeout"]) != expected:
            module.fail_json(msg="TrueNAS TLS fingerprint mismatch; no credentials transmitted.")
        with Client(a["host"], a["port"], a["endpoint"], a["timeout"]) as client:
            login = client.call("auth.login_ex", [{
                "mechanism": "API_KEY_PLAIN", "username": a["username"],
                "api_key": a["api_key"], "login_options": {"user_info": False},
            }])
            if not isinstance(login, dict) or login.get("response_type") != "SUCCESS":
                module.fail_json(msg="TrueNAS Apps write authentication failed.")
            states = {x["name"]: x for x in client.call("app.query", [[], {}])}
            plan = []
            for app in desired:
                name = app["name"]
                observed = states.get(name)
                action = "CREATE" if observed is None else "NOOP"
                if observed:
                    if observed.get("version") != app["version"]:
                        module.fail_json(msg=f"Pinned version drift for {name}; refusing automatic upgrade.")
                    cfg = client.call("app.config", [name])
                    actual = mounts(cfg)
                    intended = mounts(app["values"])
                    if actual != intended or (
                            cfg.get("run_as", {}).get("group") !=
                            app["values"]["run_as"]["group"]
                    ):
                        if name == "filebrowser-quantum":
                            if cfg.get("run_as") != app["values"]["run_as"]:
                                module.fail_json(msg="FileBrowser process identity drift: update forbidden.")
                            # Permit only the already-audited RO -> RW transition
                            # for two paths, never changing sources or destinations.
                            old_expected = sorted((
                                src, dst, src.endswith("/data") or src.endswith("/backups")
                            ) for src, dst, _ in intended)
                            if actual != old_expected:
                                module.fail_json(msg="Unexpected FileBrowser mount drift; update forbidden.")
                        action = "UPDATE"
                plan.append({"name": name, "action": action, "version": app["version"]})
                if not a["apply"] or module.check_mode or action == "NOOP":
                    continue
                if name == "filebrowser-quantum" and action == "CREATE":
                    values = json.loads(json.dumps(app["values"]))
                    values.setdefault("filebrowser", {})["admin_password"] = a["app_password"]
                    job = client.call("app.create", [{
                        "app_name": name, "catalog_app": name,
                        "train": app["train"], "version": app["version"],
                        "values": values,
                    }])
                else:
                    # Update mounts only; do not rotate FileBrowser password,
                    # change user identity, or touch unrelated app settings.
                    values = {"storage": {"additional_storage":
                                         app["values"]["storage"]["additional_storage"]}}
                    if name == "backrest":
                        values["run_as"] = app["values"]["run_as"]
                    job = client.call("app.update", [name, {"values": values}])
                wait_job(client, job)
                config = client.call("app.config", [name])
                if mounts(config) != mounts(app["values"]):
                    module.fail_json(msg=f"Mount verification failed after {name} reconciliation.")
        changed = bool(a["apply"] and not module.check_mode and any(
            p["action"] != "NOOP" for p in plan))
        module.exit_json(changed=changed, result="PASS", plan=plan)
    except RpcError as e:
        module.fail_json(msg="TrueNAS JSON-RPC method failed.", method=e.method, code=e.code)
    except Exception as e:
        module.fail_json(msg=f"TrueNAS Apps operation failed: {type(e).__name__}. No secret output.")


if __name__ == "__main__":
    main()
