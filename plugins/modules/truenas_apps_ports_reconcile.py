#!/usr/bin/python
# -*- coding: utf-8 -*-
"""Guarded TrueNAS apps network port transition; storage and identities immutable."""
from __future__ import annotations
import ipaddress

DOCUMENTATION = """
---
module: truenas_apps_ports_reconcile
short_description: Guarded publication of exact two TrueNAS web ports
description:
  - Exact site/TLS identity and read-only plan before app.update.
  - Only the web_port network subtree can change.
  - Does not mutate app identities, passwords, datasets or restic.
  - Blocks unexpected app version, ports, mounts or existing LAN bind IPs.
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
  apps:
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


MAX_WEB_APPS = 2  # Site data governs identity, mounts, ports and targets.


class RPCError(Exception):
    def __init__(self, method):
        self.method = method


class Client:
    def __init__(self, host, port, endpoint, timeout):
        self.uri = f"wss://{host}:{port}{endpoint}"
        self.timeout = timeout
        self.ws = None
        self.seq = 0

    def __enter__(self):
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        self.ws = connect(self.uri, ssl=ctx, open_timeout=self.timeout,
                          close_timeout=self.timeout)
        return self

    def __exit__(self, *_args):
        if self.ws is not None:
            self.ws.close()

    def call(self, method, args=None):
        self.seq += 1
        number = self.seq
        self.ws.send(json.dumps({"jsonrpc": "2.0", "id": number,
                                 "method": method, "params": args or []}))
        while True:
            reply = json.loads(self.ws.recv(timeout=self.timeout))
            if reply.get("id") != number:
                continue
            if reply.get("error"):
                raise RPCError(method)
            return reply.get("result")


def tls_pin(host, port, timeout):
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    with socket.create_connection((host, port), timeout=timeout) as raw:
        with ctx.wrap_socket(raw, server_hostname=host) as secure:
            return hashlib.sha256(secure.getpeercert(binary_form=True)).hexdigest().upper()


def wait_job(client, job_id, timeout=900):
    if not isinstance(job_id, int) or isinstance(job_id, bool):
        raise ValueError("INVALID_APP_JOB_ID")
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        jobs = client.call("core.get_jobs", [[["id", "=", job_id]], {}])
        if jobs:
            state = jobs[0].get("state")
            if state == "SUCCESS":
                return
            if state in {"FAILED", "ABORTED"}:
                raise ValueError("APP_UPDATE_JOB_FAILED")
        time.sleep(2)
    raise ValueError("APP_UPDATE_JOB_TIMEOUT")


def mounts(values):
    additional = ((values.get("storage") or {}).get("additional_storage") or [])
    return {
        (str((x.get("host_path_config") or {}).get("path", "")).split("/")[-1],
         x.get("mount_path"), x.get("read_only"))
        for x in additional
    }


def expected_published(port, host):
    return {"bind_mode": "published", "port_number": port, "host_ips": [host]}


def app_contract(app, pool, host):
    name = app.get("name")
    if (not isinstance(name, str) or not name
            or app.get("catalog_app") != name
            or app.get("train") != "community"
            or not app.get("version")):
        raise ValueError("APP_PIN_DRIFT")
    values = app.get("values") or {}
    identity = values.get("run_as") or {}
    if (not isinstance(identity, dict)
            or not isinstance(identity.get("user"), int)
            or not isinstance(identity.get("group"), int)
            or identity["user"] < 1 or identity["group"] < 1):
        raise ValueError("APP_IDENTITY_NOT_PINNED")
    storage = (values.get("storage") or {}).get("additional_storage") or []
    if not isinstance(storage, list) or not storage:
        raise ValueError("APP_MOUNTS_NOT_PINNED")
    for item in storage:
        location = (item.get("host_path_config") or {}).get("path")
        if (not isinstance(location, str)
                or not location.startswith("/mnt/" + pool + "/")
                or not isinstance(item.get("mount_path"), str)
                or not item["mount_path"].startswith("/")
                or not isinstance(item.get("read_only"), bool)):
            raise ValueError("APP_MOUNT_OUTSIDE_POOL")
    declared = mounts(values)
    if len(declared) != len(storage):
        raise ValueError("APP_DUPLICATE_MOUNT")
    network = values.get("network") or {}
    web = network.get("web_port") or {}
    port = web.get("port_number")
    if (not isinstance(port, int) or isinstance(port, bool)
            or not 1024 < port < 65536
            or web != expected_published(port, host)
            or network.get("host_network") is not False
            or network.get("networks") not in ([], None)):
        raise ValueError("APP_DESIRED_PORT_NOT_EXACT")
    return {"port": port, "uid": identity["user"],
            "gid": identity["group"], "mounts": declared}


def decide(cfg, app, observed, spec, host):
    if observed.get("state") != "RUNNING" or observed.get("version") != app.get("version"):
        raise ValueError("APP_NOT_RUNNING_OR_VERSION_DRIFT")
    if mounts(cfg) != spec["mounts"]:
        raise ValueError("APP_STORAGE_MOUNT_DRIFT")
    if cfg.get("run_as") != {"user": spec["uid"], "group": spec["gid"]}:
        raise ValueError("APP_PROCESS_IDENTITY_DRIFT")
    network = cfg.get("network") or {}
    if network.get("host_network") is not False or network.get("networks") not in ([], None):
        raise ValueError("APP_NETWORK_DRIFT")
    web = network.get("web_port") or {}
    if web == expected_published(spec["port"], host):
        return "NOOP"
    if web != {"bind_mode": "exposed", "port_number": spec["port"]}:
        raise ValueError("APP_WEB_PORT_OUTSIDE_EXACT_TRANSITION")
    return "UPDATE"


def validate_inventory(apps, pool, host):
    if (not isinstance(pool, str) or not pool
            or not all(c.isascii() and (c.isalnum() or c == "_") for c in pool)
            or not isinstance(apps, list) or len(apps) != MAX_WEB_APPS):
        raise ValueError("APP_SCOPE_NOT_EXACT")
    if not all(isinstance(item, dict) for item in apps):
        raise ValueError("APP_CONTRACT_NOT_MAPPING")
    names = [item.get("name") for item in apps]
    if len(set(names)) != len(names):
        raise ValueError("APP_NAMES_NOT_UNIQUE")
    specs = {item["name"]: app_contract(item, pool, host) for item in apps}
    if len({spec["port"] for spec in specs.values()}) != len(specs):
        raise ValueError("APP_WEB_PORTS_NOT_UNIQUE")
    return specs


def main():
    module = AnsibleModule(argument_spec={
        "host": {"type": "str", "required": True},
        "port": {"type": "int", "default": 443},
        "endpoint": {"type": "str", "default": "/api/current"},
        "tls_sha256_fingerprint": {"type": "str", "required": True},
        "username": {"type": "str", "required": True},
        "api_key": {"type": "str", "required": True, "no_log": True},
        "apps": {"type": "list", "elements": "dict", "required": True},
        "pool_name": {"type": "str", "required": True},
        "apply": {"type": "bool", "default": False},
        "timeout": {"type": "int", "default": 30},
    }, supports_check_mode=True)
    a = module.params
    changed = False
    try:
        if connect is None:
            raise ValueError("WEBSOCKETS_IMPORT_UNAVAILABLE")
        if (a["endpoint"] != "/api/current" or a["username"] != "ansimble"
                or not ipaddress.ip_address(a["host"]).is_private):
            raise ValueError("TRUENAS_TARGET_IDENTITY_INVALID")
        if len(a["api_key"]) < 16:
            raise ValueError("MISSING_SITE_RUNTIME_AUTH")
        specs = validate_inventory(a["apps"], a["pool_name"], a["host"])
        if tls_pin(a["host"], a["port"], a["timeout"]) != a["tls_sha256_fingerprint"].replace(":", "").upper():
            raise ValueError("TRUENAS_TLS_PIN_MISMATCH")
        with Client(a["host"], a["port"], a["endpoint"], a["timeout"]) as client:
            auth = client.call("auth.login_ex", [{
                "mechanism": "API_KEY_PLAIN",
                "username": a["username"], "api_key": a["api_key"],
                "login_options": {"user_info": False},
            }])
            if not isinstance(auth, dict) or auth.get("response_type") != "SUCCESS":
                raise ValueError("TRUENAS_WRITE_AUTH_FAILED")
            observed = {x["name"]: x for x in (client.call("app.query", [[], {}]) or [])}
            if any(name not in observed for name in specs):
                raise ValueError("APP_REQUIRED_INSTALL_MISSING")
            plan = []
            for app in a["apps"]:
                name = app["name"]
                config = client.call("app.config", [name])
                action = decide(config, app, observed[name], specs[name], a["host"])
                plan.append({"app": name, "action": action,
                             "port": specs[name]["port"], "bind_host": a["host"]})
            if not a["apply"] or module.check_mode:
                module.exit_json(result="PASS", changed=False, plan=plan,
                                 mode="PLAN", secret_values_reported=False)
            # No network changes outside this pinned allowlist.
            for app in a["apps"]:
                name = app["name"]
                if next(item for item in plan if item["app"] == name)["action"] == "NOOP":
                    continue
                port = specs[name]["port"]
                job = client.call("app.update", [
                    name, {"values": {"network": {"web_port": expected_published(port, a["host"])}}},
                ])
                wait_job(client, job)
                updated = client.call("app.config", [name])
                # Re-audit mounts and process identity after the app restart.
                if updated.get("network", {}).get("web_port") != expected_published(port, a["host"]):
                    raise ValueError("PUBLISHED_PORT_NOT_VERIFIED")
                if mounts(updated) != specs[name]["mounts"] or updated.get("run_as") != {
                    "user": specs[name]["uid"], "group": specs[name]["gid"]
                }:
                    raise ValueError("POST_UPDATE_STORAGE_OR_IDENTITY_DRIFT")
                changed = True
            # app.update can complete while the containers are still DEPLOYING.
            # Wait for actual RUNNING state plus published host-port mappings.
            ready_until = time.monotonic() + 180
            while True:
                state = {x["name"]: x for x in (client.call("app.query", [[], {}]) or [])}
                ready = all(
                    state.get(name, {}).get("state") == "RUNNING" and any(
                        any(h.get("host_ip") == a["host"] and h.get("host_port") == spec["port"]
                            for h in port.get("host_ports") or [])
                        for port in state.get(name, {}).get("active_workloads", {}).get("used_ports") or []
                    )
                    for name, spec in specs.items()
                )
                if ready:
                    break
                if time.monotonic() > ready_until:
                    raise ValueError("APP_PORTS_NOT_READY_AFTER_UPDATE_TIMEOUT")
                time.sleep(3)
        module.exit_json(result="PASS", changed=changed, mode="APPLY",
                         plan=plan, secret_values_reported=False)
    except Exception as exc:
        module.fail_json(msg="TRUENAS_APPS_PORTS_BLOCK",
                         reason=(str(exc) if isinstance(exc, ValueError) else type(exc).__name__),
                         changed=changed, secret_values_reported=False)


if __name__ == "__main__":
    main()
