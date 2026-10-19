#!/usr/bin/python
# -*- coding: utf-8 -*-
from __future__ import annotations

DOCUMENTATION = r'''
---
module: truenas_storage_reconcile
short_description: Reconcile TrueNAS pool and base storage layout over pinned WebSocket API
description:
  - Authenticates with a permanent user-linked API key.
  - Excludes boot-pool disks and treats all remaining disks as the data storage domain.
  - Supports explicit create or import pool actions.
  - Blocks single-disk topology when more or fewer than one data disk is present.
  - Reconciles datasets and ordinary directories without SSH.
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
  action:
    type: str
    choices: [discover, reconcile]
    default: reconcile
  pool_action:
    type: str
    choices: [create, import]
    required: true
  pool_name:
    type: str
    required: true
  topology:
    type: str
    choices: [single_disk]
    required: true
  allow_declared_storage_reset:
    type: bool
    default: false
  datasets:
    type: list
    elements: str
    default: []
  directories:
    type: list
    elements: str
    default: []
  timeout:
    type: int
    default: 600
author:
  - DigitalHouses
'''

import hashlib
import json
import os
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
        self.ws = None
        self.next_id = 1

    def __enter__(self):
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        self.ws = connect(
            f"wss://{self.host}:{self.port}{self.endpoint}",
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


def login_api_key(client, username, api_key):
    result = client.call(
        "auth.login_ex",
        [{
            "mechanism": "API_KEY_PLAIN",
            "username": username,
            "api_key": api_key,
            "login_options": {"user_info": False},
        }],
    )
    return isinstance(result, dict) and result.get("response_type") == "SUCCESS"


def query(client, method, filters=None, options=None):
    result = client.call(method, [filters or [], options or {}])
    return result if isinstance(result, list) else []


def wait_job(client, job_id, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        jobs = query(client, "core.get_jobs", [["id", "=", int(job_id)]], {})
        if jobs:
            job = jobs[0]
            state = str(job.get("state") or "").upper()
            if state == "SUCCESS":
                return job.get("result")
            if state in {"FAILED", "ABORTED"}:
                raise RuntimeError(
                    f"TrueNAS job {job_id} ended in {state}: "
                    f"{job.get('error') or job.get('exception') or 'unknown error'}"
                )
        time.sleep(1)
    raise TimeoutError(f"Timed out waiting for TrueNAS job {job_id}.")


def call_job(client, method, params, timeout):
    result = client.call(method, params)
    if isinstance(result, int) and not isinstance(result, bool):
        return wait_job(client, result, timeout)
    return result


def safe_rpc_error(exc):
    payload = exc.payload if isinstance(exc.payload, dict) else {}
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    return {
        "method": exc.method,
        "code": payload.get("code"),
        "message": payload.get("message"),
        "errname": data.get("errname"),
        "reason": data.get("reason"),
    }


def disk_summary(disks):
    return [
        {
            "name": d.get("name"),
            "devname": d.get("devname"),
            "model": d.get("model"),
            "serial": d.get("serial"),
            "size": d.get("size"),
            "bus": d.get("bus"),
            "pool": d.get("pool"),
            "zfs_guid": d.get("zfs_guid"),
        }
        for d in disks
    ]


def importable_summary(pools):
    return [
        {
            "name": item.get("name"),
            "guid": item.get("guid"),
            "status": item.get("status"),
            "hostname": item.get("hostname"),
        }
        for item in (pools or [])
    ]


def dataset_exists(client, full_name):
    existing = query(
        client,
        "pool.dataset.query",
        [["id", "=", full_name]],
        {"extra": {"flat": True}},
    )
    return len(existing) == 1


def ensure_dataset(client, full_name):
    if dataset_exists(client, full_name):
        return False
    client.call("pool.dataset.create", [{"name": full_name}])
    return True


def directory_state(client, full_path):
    parent = os.path.dirname(full_path)
    base = os.path.basename(full_path)
    try:
        entries = client.call(
            "filesystem.listdir",
            [parent, [], {"select": ["name", "type"]}],
        )
    except RpcError:
        return "MISSING_PARENT"
    matches = [e for e in (entries or []) if e.get("name") == base]
    if len(matches) > 1:
        raise RuntimeError(f"Multiple filesystem entries match {full_path!r}.")
    if not matches:
        return "MISSING"
    if matches[0].get("type") != "DIRECTORY":
        return "WRONG_TYPE"
    return "DIRECTORY"


def ensure_directory(client, full_path):
    state = directory_state(client, full_path)
    if state == "DIRECTORY":
        return False
    if state == "WRONG_TYPE":
        raise RuntimeError(f"{full_path!r} exists but is not a directory.")
    if state == "MISSING_PARENT":
        raise RuntimeError(f"Parent directory for {full_path!r} does not exist.")
    client.call("filesystem.mkdir", [{"path": full_path, "options": {"mode": "755"}}])
    return True


def main():
    module = AnsibleModule(
        argument_spec=dict(
            action=dict(type="str", default="reconcile", choices=["discover", "reconcile"]),
            host=dict(type="str", required=True),
            port=dict(type="int", default=443),
            endpoint=dict(type="str", default="/api/current"),
            tls_sha256_fingerprint=dict(type="str", required=True),
            username=dict(type="str", required=True),
            api_key=dict(type="str", required=True, no_log=True),
            pool_action=dict(type="str", required=True, choices=["create", "import"]),
            pool_name=dict(type="str", required=True),
            topology=dict(type="str", required=True, choices=["single_disk"]),
            allow_declared_storage_reset=dict(type="bool", default=False),
            datasets=dict(type="list", elements="str", default=[]),
            directories=dict(type="list", elements="str", default=[]),
            timeout=dict(type="int", default=600),
        ),
        supports_check_mode=False,
    )

    if connect is None:
        module.fail_json(msg="Python dependency 'websockets' is required.")

    host = module.params["host"]
    port = module.params["port"]
    endpoint = module.params["endpoint"]
    timeout = module.params["timeout"]
    expected = normalize_fingerprint(module.params["tls_sha256_fingerprint"])

    try:
        actual = tls_peer_fingerprint(host, port, timeout)
    except Exception as exc:
        module.fail_json(msg=f"Unable to read TrueNAS TLS certificate: {type(exc).__name__}: {exc}")

    if actual != expected:
        module.fail_json(
            msg="TrueNAS TLS fingerprint mismatch. Authentication was not attempted.",
            expected_tls_sha256=colonize(expected),
            actual_tls_sha256=colonize(actual),
        )

    changed = False

    try:
        with Client(host, port, endpoint, timeout) as client:
            if not login_api_key(client, module.params["username"], module.params["api_key"]):
                module.fail_json(msg="TrueNAS write API authentication failed.")

            boot_disks = set(client.call("boot.get_disks") or [])
            all_disks = query(
                client,
                "disk.query",
                [],
                {"extra": {"pools": True}},
            )
            data_disks = [
                disk for disk in all_disks
                if disk.get("name") not in boot_disks
            ]

            pools = query(client, "pool.query", [["name", "=", module.params["pool_name"]]])
            if len(pools) > 1:
                module.fail_json(msg="Multiple pools match the desired pool name.")

            importable = []
            if not pools:
                importable = call_job(client, "pool.import_find", [], timeout) or []

            if module.params["action"] == "discover":
                desired_dataset_ids = [
                    f"{module.params['pool_name']}/{relative}".strip("/")
                    for relative in module.params["datasets"]
                ]
                existing_dataset_ids = [
                    dataset_id
                    for dataset_id in desired_dataset_ids
                    if dataset_exists(client, dataset_id)
                ]
                desired_directory_paths = [
                    f"/mnt/{module.params['pool_name']}/{relative}".rstrip("/")
                    for relative in module.params["directories"]
                ]
                directory_states = {
                    path: directory_state(client, path)
                    for path in desired_directory_paths
                } if len(pools) == 1 else {
                    path: "MISSING_PARENT"
                    for path in desired_directory_paths
                }

                module.exit_json(
                    changed=False,
                    tls_sha256_fingerprint=colonize(actual),
                    boot_disks=sorted(boot_disks),
                    data_disks=disk_summary(data_disks),
                    importable_pools=importable_summary(importable),
                    desired_pool=(
                        {
                            "name": pools[0].get("name"),
                            "guid": pools[0].get("guid"),
                            "status": pools[0].get("status"),
                        }
                        if len(pools) == 1
                        else None
                    ),
                    existing_datasets=existing_dataset_ids,
                    missing_datasets=[
                        item for item in desired_dataset_ids
                        if item not in existing_dataset_ids
                    ],
                    directory_states=directory_states,
                    topology=module.params["topology"],
                )

            if module.params["topology"] == "single_disk" and len(data_disks) != 1:
                module.fail_json(
                    msg=(
                        "Single-disk topology gate failed: exactly one non-boot "
                        "data disk must be visible. Multi-disk topology requires "
                        "a separate declared plan."
                    ),
                    boot_disks=sorted(boot_disks),
                    data_disks=disk_summary(data_disks),
                )

            if not pools:
                if module.params["pool_action"] == "create":
                    if importable and not module.params["allow_declared_storage_reset"]:
                        module.fail_json(
                            msg=(
                                "Importable pool metadata exists but declared storage "
                                "reset is not authorized."
                            ),
                            importable_pools=importable_summary(importable),
                            data_disks=disk_summary(data_disks),
                        )

                    disk_name = data_disks[0]["name"]
                    call_job(client, "disk.wipe", [disk_name, "QUICK", True], timeout)
                    call_job(
                        client,
                        "pool.create",
                        [{
                            "name": module.params["pool_name"],
                            "topology": {
                                "data": [{
                                    "type": "STRIPE",
                                    "disks": [disk_name],
                                }]
                            },
                        }],
                        timeout,
                    )
                    changed = True

                elif module.params["pool_action"] == "import":
                    candidates = [
                        item for item in importable
                        if item.get("name") == module.params["pool_name"]
                    ]
                    if len(candidates) != 1:
                        module.fail_json(
                            msg=(
                                "Import action requires exactly one importable pool "
                                "matching the declared pool name."
                            ),
                            importable_pools=importable_summary(importable),
                        )
                    call_job(
                        client,
                        "pool.import_pool",
                        [{
                            "guid": str(candidates[0]["guid"]),
                            "name": module.params["pool_name"],
                        }],
                        timeout,
                    )
                    changed = True

            pools = query(client, "pool.query", [["name", "=", module.params["pool_name"]]])
            if len(pools) != 1:
                module.fail_json(msg="Desired pool is not uniquely observable after reconciliation.")

            pool = pools[0]
            if pool.get("status") != "ONLINE":
                module.fail_json(
                    msg="Desired pool exists but is not ONLINE.",
                    pool={"name": pool.get("name"), "guid": pool.get("guid"), "status": pool.get("status")},
                )

            for relative in module.params["datasets"]:
                full_name = f"{module.params['pool_name']}/{relative}".strip("/")
                changed = ensure_dataset(client, full_name) or changed

            for relative in module.params["directories"]:
                full_path = f"/mnt/{module.params['pool_name']}/{relative}".rstrip("/")
                changed = ensure_directory(client, full_path) or changed

            missing_datasets = [
                full_name
                for full_name in [
                    f"{module.params['pool_name']}/{relative}".strip("/")
                    for relative in module.params["datasets"]
                ]
                if not dataset_exists(client, full_name)
            ]
            if missing_datasets:
                module.fail_json(msg="Dataset verification failed.", missing_datasets=missing_datasets)

            for relative in module.params["directories"]:
                full_path = f"/mnt/{module.params['pool_name']}/{relative}".rstrip("/")
                parent = os.path.dirname(full_path)
                base = os.path.basename(full_path)
                entries = client.call(
                    "filesystem.listdir",
                    [parent, [], {"select": ["name", "type"]}],
                )
                if not any(e.get("name") == base and e.get("type") == "DIRECTORY" for e in (entries or [])):
                    module.fail_json(msg="Directory verification failed.", missing_directory=full_path)

    except RpcError as exc:
        module.fail_json(
            msg="TrueNAS storage JSON-RPC method failed.",
            rpc_error=safe_rpc_error(exc),
            data_disks=disk_summary(data_disks) if "data_disks" in locals() else [],
        )
    except Exception as exc:
        module.fail_json(msg=f"TrueNAS storage reconciliation failed: {type(exc).__name__}: {exc}")

    module.exit_json(
        changed=changed,
        tls_sha256_fingerprint=colonize(actual),
        pool={
            "name": pool.get("name"),
            "guid": pool.get("guid"),
            "status": pool.get("status"),
        },
        boot_disks=sorted(boot_disks),
        data_disks=disk_summary(data_disks),
        importable_pools=importable,
        topology=module.params["topology"],
        datasets=module.params["datasets"],
        directories=module.params["directories"],
    )


if __name__ == "__main__":
    main()
