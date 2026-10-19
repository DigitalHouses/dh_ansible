#!/usr/bin/python
# -*- coding: utf-8 -*-
from __future__ import annotations

DOCUMENTATION = r'''
---
module: truenas_installer_rpc
short_description: Discover and drive the official TrueNAS installer WebSocket API
description:
  - Discovers the official TrueNAS installer on a bootstrap subnet.
  - Uses the installer's JSON-RPC WebSocket API rather than console keystroke automation.
  - Requires exactly one matching installer and exactly one non-removable install disk.
  - The data SATA controller should be attached only after OS installation.
options:
  action:
    type: str
    choices: [discover, install]
    required: true
  cidr:
    type: str
    required: true
  expected_version:
    type: str
    required: true
  port:
    type: int
    default: 80
  websocket_path:
    type: str
    default: /ws
  connect_timeout:
    type: float
    default: 0.5
  discovery_timeout:
    type: int
    default: 180
  install_timeout:
    type: int
    default: 1800
  admin_username:
    type: str
    default: truenas_admin
  admin_password:
    type: str
    no_log: true
  target_ipv4:
    type: str
  reboot_after_install:
    type: bool
    default: true
author:
  - DigitalHouses
'''

EXAMPLES = r'''
- name: Discover TrueNAS installer
  truenas_installer_rpc:
    action: discover
    cidr: 198.51.100.0/24
    expected_version: "25.10.7"

- name: Install TrueNAS to the only visible system disk
  truenas_installer_rpc:
    action: install
    cidr: 198.51.100.0/24
    expected_version: "25.10.7"
    admin_password: "{{ truenas_bootstrap_password }}"
    target_ipv4: "198.51.100.232/24"
'''

RETURN = r'''
installer_ip:
  type: str
  returned: success
system_info:
  type: dict
  returned: success
install_disk:
  type: dict
  returned: action=install
network_interface:
  type: str
  returned: action=install
'''

import ipaddress
import json
import socket
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from ansible.module_utils.basic import AnsibleModule

try:
    from websockets.sync.client import connect
except ImportError:
    connect = None


class RpcError(Exception):
    def __init__(self, method, error):
        self.method = method
        self.error = error
        super().__init__(method)


class InstallerClient:
    def __init__(self, host, port, path, timeout):
        self.uri = f"ws://{host}:{port}{path}"
        self.timeout = timeout
        self.ws = None
        self.next_id = 1
        self.progress = []

    def __enter__(self):
        self.ws = connect(
            self.uri,
            open_timeout=self.timeout,
            close_timeout=min(self.timeout, 10),
        )
        return self

    def __exit__(self, exc_type, exc, tb):
        if self.ws is not None:
            self.ws.close()

    def call(self, method, params_marker=None):
        request_id = self.next_id
        self.next_id += 1
        request = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
        }
        if params_marker is not None:
            request["params"] = [params_marker]
        self.ws.send(json.dumps(request))

        while True:
            raw = self.ws.recv(timeout=self.timeout)
            response = json.loads(raw)

            if response.get("method") == "installation_progress":
                params = response.get("params") or []
                if params and isinstance(params[0], dict):
                    self.progress.append({
                        "progress": params[0].get("progress"),
                        "message": params[0].get("message"),
                    })
                continue

            if response.get("id") != request_id:
                continue

            if "error" in response:
                raise RpcError(method, response["error"])

            return response.get("result")


def tcp_open(host, port, timeout):
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def version_matches(actual, expected):
    actual = str(actual or "")
    expected = str(expected)
    return actual == expected or actual.endswith(expected) or expected in actual


def probe_installer(host, port, path, timeout, expected_version):
    try:
        with InstallerClient(host, port, path, max(timeout, 1.0)) as client:
            info = client.call("system_info")
        if isinstance(info, dict) and version_matches(info.get("version"), expected_version):
            return {"host": host, "system_info": info}
    except Exception:
        return None
    return None


def discover(cidr, port, path, connect_timeout, discovery_timeout, expected_version):
    network = ipaddress.ip_network(cidr, strict=False)
    deadline = time.monotonic() + discovery_timeout

    while time.monotonic() < deadline:
        hosts = [str(ip) for ip in network.hosts()]
        open_hosts = []

        with ThreadPoolExecutor(max_workers=min(64, max(1, len(hosts)))) as pool:
            futures = {
                pool.submit(tcp_open, host, port, connect_timeout): host
                for host in hosts
            }
            for future in as_completed(futures):
                if future.result():
                    open_hosts.append(futures[future])

        matches = []
        with ThreadPoolExecutor(max_workers=min(16, max(1, len(open_hosts)))) as pool:
            futures = {
                pool.submit(
                    probe_installer,
                    host,
                    port,
                    path,
                    max(connect_timeout, 1.0),
                    expected_version,
                ): host
                for host in open_hosts
            }
            for future in as_completed(futures):
                result = future.result()
                if result:
                    matches.append(result)

        if len(matches) == 1:
            return matches[0]

        if len(matches) > 1:
            raise RuntimeError(
                "Multiple matching TrueNAS installers found: "
                + ", ".join(sorted(item["host"] for item in matches))
            )

        time.sleep(3)

    raise RuntimeError(
        f"No TrueNAS installer matching version {expected_version!r} "
        f"was found in {cidr}"
    )


def safe_rpc_error(exc):
    payload = exc.error if isinstance(exc.error, dict) else {}
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    return {
        "method": exc.method,
        "code": payload.get("code"),
        "message": payload.get("message"),
        "errno": data.get("errno"),
    }


def main():
    module = AnsibleModule(
        argument_spec=dict(
            action=dict(type="str", required=True, choices=["discover", "install"]),
            cidr=dict(type="str", required=True),
            expected_version=dict(type="str", required=True),
            port=dict(type="int", default=80),
            websocket_path=dict(type="str", default="/ws"),
            connect_timeout=dict(type="float", default=0.5),
            discovery_timeout=dict(type="int", default=180),
            install_timeout=dict(type="int", default=1800),
            admin_username=dict(type="str", default="truenas_admin"),
            admin_password=dict(type="str", no_log=True),
            target_ipv4=dict(type="str"),
            reboot_after_install=dict(type="bool", default=True),
        ),
        required_if=[
            ("action", "install", ["admin_password", "target_ipv4"]),
        ],
        supports_check_mode=False,
    )

    if connect is None:
        module.fail_json(msg="Python dependency 'websockets' is required.")

    try:
        candidate = discover(
            module.params["cidr"],
            module.params["port"],
            module.params["websocket_path"],
            module.params["connect_timeout"],
            module.params["discovery_timeout"],
            module.params["expected_version"],
        )
    except Exception as exc:
        module.fail_json(msg=f"TrueNAS installer discovery failed: {exc}")

    if module.params["action"] == "discover":
        module.exit_json(
            changed=False,
            installer_ip=candidate["host"],
            system_info=candidate["system_info"],
        )

    target = ipaddress.ip_interface(module.params["target_ipv4"])

    try:
        with InstallerClient(
            candidate["host"],
            module.params["port"],
            module.params["websocket_path"],
            module.params["install_timeout"],
        ) as client:
            adopted = client.call("is_adopted")
            if adopted:
                module.fail_json(
                    msg=(
                        "TrueNAS installer is already adopted by another or a "
                        "previous controller session. Reconcile/reboot the installer "
                        "before retrying; no blind takeover is allowed."
                    ),
                    installer_ip=candidate["host"],
                )

            client.call("adopt")
            disks = client.call("list_disks")
            interfaces = client.call("list_network_interfaces")

            install_disks = [
                disk for disk in (disks or [])
                if not disk.get("removable", False)
            ]

            if len(install_disks) != 1:
                module.fail_json(
                    msg=(
                        "Installer disk gate failed: exactly one non-removable "
                        "disk must be visible during OS installation. The data "
                        "controller must remain detached until post-install."
                    ),
                    installer_ip=candidate["host"],
                    disks=[
                        {
                            "name": d.get("name"),
                            "size": d.get("size"),
                            "model": d.get("model"),
                            "label": d.get("label"),
                            "removable": d.get("removable"),
                        }
                        for d in (disks or [])
                    ],
                )

            if len(interfaces or []) != 1:
                module.fail_json(
                    msg="Installer network gate failed: exactly one interface is required.",
                    installer_ip=candidate["host"],
                    interfaces=interfaces,
                )

            disk = install_disks[0]
            interface = interfaces[0]["name"]

            params = {
                "disks": [disk["name"]],
                "set_pmbr": False,
                "authentication": {
                    "username": module.params["admin_username"],
                    "password": module.params["admin_password"],
                },
                "post_install": {
                    "network_interfaces": [
                        {
                            "name": interface,
                            "aliases": [
                                {
                                    "type": "INET",
                                    "address": str(target.ip),
                                    "netmask": int(target.network.prefixlen),
                                }
                            ],
                            "ipv4_dhcp": False,
                            "ipv6_auto": False,
                        }
                    ]
                },
            }

            client.call("install", params)

            if module.params["reboot_after_install"]:
                client.call("reboot")

            progress_tail = client.progress[-10:]

    except RpcError as exc:
        module.fail_json(
            msg="TrueNAS installer JSON-RPC method failed.",
            rpc_error=safe_rpc_error(exc),
            installer_ip=candidate["host"],
        )
    except Exception as exc:
        module.fail_json(
            msg=f"TrueNAS installer operation failed: {type(exc).__name__}: {exc}",
            installer_ip=candidate["host"],
        )

    module.exit_json(
        changed=True,
        installer_ip=candidate["host"],
        system_info=candidate["system_info"],
        install_disk={
            "name": disk.get("name"),
            "size": disk.get("size"),
            "model": disk.get("model"),
            "label": disk.get("label"),
        },
        network_interface=interface,
        target_ipv4=module.params["target_ipv4"],
        progress_tail=progress_tail,
        reboot_requested=module.params["reboot_after_install"],
    )


if __name__ == "__main__":
    main()
