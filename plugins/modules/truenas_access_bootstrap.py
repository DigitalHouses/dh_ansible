#!/usr/bin/python
# -*- coding: utf-8 -*-
from __future__ import annotations

DOCUMENTATION = r'''
---
module: truenas_access_bootstrap
short_description: Bootstrap permanent TrueNAS API identities over JSON-RPC WebSocket
description:
  - Uses a temporary installer-created administrator password only during bootstrap.
  - Reuses TrueNAS built-in administrative groups discovered by assigned role.
  - Creates only the two dedicated service users; no custom privilege objects.
  - Creates or resets named bootstrap-owned API keys so interrupted runs never depend on an unrecoverable key value.
  - Verifies both new API keys before returning them.
  - Can lock the temporary bootstrap administrator after encrypted key persistence succeeds.
options:
  action:
    type: str
    choices: [provision, finalize, lock_bootstrap]
    required: true
  host:
    type: str
    required: true
  port:
    type: int
    default: 443
  endpoint:
    type: str
    default: /api/current
  timeout:
    type: int
    default: 20
  expected_version:
    type: str
  tls_sha256_fingerprint:
    type: str
  trust_on_first_use:
    type: bool
    default: false
  bootstrap_username:
    type: str
    default: truenas_admin
  bootstrap_password:
    type: str
    no_log: true
  audit_username:
    type: str
    default: ansimble-audit
  audit_role:
    type: str
    default: READONLY_ADMIN
  write_username:
    type: str
    default: ansimble
  write_role:
    type: str
    default: FULL_ADMIN
  audit_api_key:
    type: str
    no_log: true
  write_api_key:
    type: str
    no_log: true
  timezone:
    type: str
  hostname:
    type: str
  ipv4_gateway:
    type: str
  nameservers:
    type: list
    elements: str
    default: []
author:
  - DigitalHouses
'''

EXAMPLES = r'''
- name: Create permanent service identities
  truenas_access_bootstrap:
    action: provision
    host: 198.51.100.232
    trust_on_first_use: true
    bootstrap_password: "{{ truenas_bootstrap_password }}"

- name: Disable the temporary bootstrap administrator
  truenas_access_bootstrap:
    action: lock_bootstrap
    host: 198.51.100.232
    tls_sha256_fingerprint: "{{ truenas_bootstrap.tls_sha256_fingerprint }}"
    write_api_key: "{{ truenas_bootstrap.write_api_key }}"
'''

RETURN = r'''
tls_sha256_fingerprint:
  type: str
  returned: success
audit_api_key:
  type: str
  returned: action=provision
write_api_key:
  type: str
  returned: action=provision
audit_verified:
  type: bool
  returned: action=provision
write_verified:
  type: bool
  returned: action=provision
bootstrap_locked:
  type: bool
  returned: action=lock_bootstrap
'''

import hashlib
import json
import socket
import ssl

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
        self.ws = None
        self.next_id = 1

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
        request = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params or [],
        }
        self.ws.send(json.dumps(request))
        while True:
            response = json.loads(self.ws.recv(timeout=self.timeout))
            if response.get("id") != request_id:
                continue
            if "error" in response:
                raise RpcError(method, response["error"])
            return response.get("result")


def version_matches(actual, expected):
    actual = str(actual or "")
    expected = str(expected or "")
    return actual == expected or actual.endswith(expected) or expected in actual


def login_password(client, username, password):
    result = client.call(
        "auth.login_ex",
        [{
            "mechanism": "PASSWORD_PLAIN",
            "username": username,
            "password": password,
            "login_options": {"user_info": False},
        }],
    )
    return isinstance(result, dict) and result.get("response_type") == "SUCCESS"


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


def query(client, method, filters):
    result = client.call(method, [filters, {}])
    return result if isinstance(result, list) else []


def select_builtin_role_group(client, role):
    groups = query(client, "group.query", [])
    candidates = [
        item for item in groups
        if item.get("local")
        and item.get("builtin")
        and role in (item.get("roles") or [])
    ]
    if len(candidates) != 1:
        raise RuntimeError(
            f"Expected exactly one built-in local group for role {role!r}, "
            f"found {len(candidates)}."
        )
    return candidates[0]


def ensure_service_identity(client, username, role):
    role_group = select_builtin_role_group(client, role)
    users = query(
        client,
        "user.query",
        [["username", "=", username], ["local", "=", True]],
    )
    if len(users) > 1:
        raise RuntimeError(f"Multiple TrueNAS users match {username!r}.")

    if users:
        user = users[0]
        problems = []
        if role not in (user.get("roles") or []):
            problems.append(f"missing role {role}")
        if not user.get("password_disabled", False):
            problems.append("password authentication enabled")
        if user.get("ssh_password_enabled", False):
            problems.append("SSH password authentication enabled")
        if user.get("smb", False):
            problems.append("SMB enabled")
        if user.get("shell") != "/usr/sbin/nologin":
            problems.append("interactive shell enabled")
        if user.get("locked", False):
            problems.append("account locked")
        if problems:
            raise RuntimeError(
                f"Existing service user {username!r} violates contract: "
                + "; ".join(problems)
            )
    else:
        client.call(
            "user.create",
            [{
                "username": username,
                "full_name": f"DigitalHouses Ansimble service account ({role})",
                "smb": False,
                "group_create": True,
                "groups": [int(role_group["id"])],
                "password_disabled": True,
                "ssh_password_enabled": False,
                "shell": "/usr/sbin/nologin",
            }],
        )
        users = query(
            client,
            "user.query",
            [["username", "=", username], ["local", "=", True]],
        )
        if len(users) != 1:
            raise RuntimeError(
                f"Service user {username!r} was not observable after create."
            )
        user = users[0]
        if role not in (user.get("roles") or []):
            raise RuntimeError(
                f"New service user {username!r} did not receive role {role!r}."
            )

    key_name = f"ansimble-{username}"
    keys = query(
        client,
        "api_key.query",
        [["username", "=", username], ["name", "=", key_name]],
    )
    if len(keys) > 1:
        raise RuntimeError(
            f"Multiple bootstrap-owned API keys named {key_name!r} exist."
        )

    if keys:
        created_key = client.call(
            "api_key.update",
            [int(keys[0]["id"]), {"reset": True}],
        )
    else:
        created_key = client.call(
            "api_key.create",
            [{
                "name": key_name,
                "username": username,
                "expires_at": None,
            }],
        )

    if not isinstance(created_key, dict) or not created_key.get("key"):
        raise RuntimeError(
            f"TrueNAS did not return an API key value for {username!r}."
        )

    return created_key["key"]

def verify_key(host, port, endpoint, timeout, username, key, expected_fingerprint):
    actual_fingerprint = tls_peer_fingerprint(host, port, timeout)
    if normalize_fingerprint(actual_fingerprint) != normalize_fingerprint(expected_fingerprint):
        return False

    with Client(host, port, endpoint, timeout) as client:
        if not login_api_key(client, username, key):
            return False
        info = client.call("system.info")
        return isinstance(info, dict) and bool(info.get("version"))


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


def main():
    module = AnsibleModule(
        argument_spec=dict(
            action=dict(type="str", required=True, choices=["provision", "finalize", "lock_bootstrap"]),
            host=dict(type="str", required=True),
            port=dict(type="int", default=443),
            endpoint=dict(type="str", default="/api/current"),
            timeout=dict(type="int", default=20),
            expected_version=dict(type="str"),
            tls_sha256_fingerprint=dict(type="str"),
            trust_on_first_use=dict(type="bool", default=False),
            bootstrap_username=dict(type="str", default="truenas_admin"),
            bootstrap_password=dict(type="str", no_log=True),
            audit_username=dict(type="str", default="ansimble-audit"),
            audit_role=dict(type="str", default="READONLY_ADMIN"),
            write_username=dict(type="str", default="ansimble"),
            write_role=dict(type="str", default="FULL_ADMIN"),
            audit_api_key=dict(type="str", no_log=True),
            write_api_key=dict(type="str", no_log=True),
            timezone=dict(type="str"),
            hostname=dict(type="str"),
            ipv4_gateway=dict(type="str"),
            nameservers=dict(type="list", elements="str", default=[]),
        ),
        required_if=[
            ("action", "provision", ["bootstrap_password", "expected_version"]),
            ("action", "finalize", ["audit_api_key", "write_api_key", "expected_version"]),
            ("action", "lock_bootstrap", ["write_api_key", "tls_sha256_fingerprint"]),
        ],
        supports_check_mode=False,
    )

    if connect is None:
        module.fail_json(msg="Python dependency 'websockets' is required.")

    host = module.params["host"]
    port = module.params["port"]
    endpoint = module.params["endpoint"]
    timeout = module.params["timeout"]

    try:
        actual = tls_peer_fingerprint(host, port, timeout)
    except Exception as exc:
        module.fail_json(msg=f"Unable to read TrueNAS TLS certificate: {type(exc).__name__}: {exc}")

    expected = normalize_fingerprint(module.params.get("tls_sha256_fingerprint"))
    if expected:
        if actual != expected:
            module.fail_json(
                msg="TrueNAS TLS fingerprint mismatch. Authentication was not attempted.",
                expected_tls_sha256=colonize(expected),
                actual_tls_sha256=colonize(actual),
            )
    elif not module.params["trust_on_first_use"]:
        module.fail_json(
            msg=(
                "TrueNAS TLS fingerprint is not pinned and trust_on_first_use is false. "
                "Authentication was not attempted."
            )
        )

    try:
        if module.params["action"] == "provision":
            with Client(host, port, endpoint, timeout) as client:
                if not login_password(
                    client,
                    module.params["bootstrap_username"],
                    module.params["bootstrap_password"],
                ):
                    module.fail_json(
                        msg="Temporary TrueNAS bootstrap authentication failed.",
                        actual_tls_sha256=colonize(actual),
                    )

                info = client.call("system.info")
                if (
                    not isinstance(info, dict)
                    or not version_matches(
                        info.get("version"),
                        module.params["expected_version"],
                    )
                ):
                    module.fail_json(
                        msg="Installed TrueNAS version does not match the pinned release.",
                        expected_version=module.params["expected_version"],
                        observed_version=(
                            info.get("version")
                            if isinstance(info, dict)
                            else None
                        ),
                        actual_tls_sha256=colonize(actual),
                    )

                network_update = {}
                if module.params.get("hostname"):
                    network_update["hostname"] = module.params["hostname"]
                if module.params.get("ipv4_gateway"):
                    network_update["ipv4gateway"] = module.params["ipv4_gateway"]
                for index, value in enumerate(module.params.get("nameservers") or [], start=1):
                    if index <= 3:
                        network_update[f"nameserver{index}"] = value
                if network_update:
                    client.call("network.configuration.update", [network_update])

                audit_key = ensure_service_identity(
                    client,
                    module.params["audit_username"],
                    module.params["audit_role"],
                )
                write_key = ensure_service_identity(
                    client,
                    module.params["write_username"],
                    module.params["write_role"],
                )

            audit_ok = verify_key(
                host, port, endpoint, timeout,
                module.params["audit_username"], audit_key, actual,
            )
            write_ok = verify_key(
                host, port, endpoint, timeout,
                module.params["write_username"], write_key, actual,
            )
            if not audit_ok or not write_ok:
                module.fail_json(
                    msg="New TrueNAS API key verification failed.",
                    audit_verified=audit_ok,
                    write_verified=write_ok,
                    actual_tls_sha256=colonize(actual),
                )

            module.exit_json(
                changed=True,
                tls_sha256_fingerprint=colonize(actual),
                audit_api_key=audit_key,
                write_api_key=write_key,
                audit_verified=True,
                write_verified=True,
            )

        if module.params["action"] == "finalize":
            audit_ok = verify_key(
                host,
                port,
                endpoint,
                timeout,
                module.params["audit_username"],
                module.params["audit_api_key"],
                actual,
            )
            write_ok = verify_key(
                host,
                port,
                endpoint,
                timeout,
                module.params["write_username"],
                module.params["write_api_key"],
                actual,
            )

            if not audit_ok or not write_ok:
                module.fail_json(
                    msg="Recovered TrueNAS API key verification failed.",
                    audit_verified=audit_ok,
                    write_verified=write_ok,
                    actual_tls_sha256=colonize(actual),
                )

            with Client(host, port, endpoint, timeout) as client:
                if not login_api_key(
                    client,
                    module.params["write_username"],
                    module.params["write_api_key"],
                ):
                    module.fail_json(
                        msg="Permanent TrueNAS write API authentication failed."
                    )

                info = client.call("system.info")
                if (
                    not isinstance(info, dict)
                    or not version_matches(
                        info.get("version"),
                        module.params["expected_version"],
                    )
                ):
                    module.fail_json(
                        msg="Installed TrueNAS version does not match the pinned release.",
                        expected_version=module.params["expected_version"],
                        observed_version=(
                            info.get("version")
                            if isinstance(info, dict)
                            else None
                        ),
                    )

                if module.params.get("timezone"):
                    client.call(
                        "system.general.update",
                        [{"timezone": module.params["timezone"]}],
                    )

                users = query(
                    client,
                    "user.query",
                    [["username", "=", module.params["bootstrap_username"]]],
                )
                if len(users) != 1:
                    module.fail_json(
                        msg="Temporary bootstrap user cannot be uniquely identified.",
                        matches=len(users),
                    )

                if not users[0].get("locked", False):
                    client.call(
                        "user.update",
                        [
                            users[0]["id"],
                            {
                                "locked": True,
                            },
                        ],
                    )

            module.exit_json(
                changed=True,
                tls_sha256_fingerprint=colonize(actual),
                audit_verified=True,
                write_verified=True,
                bootstrap_locked=True,
                timezone=module.params.get("timezone"),
            )

        with Client(host, port, endpoint, timeout) as client:
            if not login_api_key(
                client,
                module.params["write_username"],
                module.params["write_api_key"],
            ):
                module.fail_json(msg="Permanent TrueNAS write API authentication failed.")

            users = query(
                client,
                "user.query",
                [["username", "=", module.params["bootstrap_username"]]],
            )
            if len(users) != 1:
                module.fail_json(
                    msg="Temporary bootstrap user cannot be uniquely identified.",
                    matches=len(users),
                )

            client.call(
                "user.update",
                [
                    users[0]["id"],
                    {
                        "locked": True,
                        "password_disabled": True,
                        "ssh_password_enabled": False,
                    },
                ],
            )

        module.exit_json(
            changed=True,
            tls_sha256_fingerprint=colonize(actual),
            bootstrap_locked=True,
        )

    except RpcError as exc:
        module.fail_json(
            msg="TrueNAS JSON-RPC bootstrap method failed.",
            rpc_error=safe_rpc_error(exc),
            actual_tls_sha256=colonize(actual),
        )
    except Exception as exc:
        module.fail_json(
            msg=f"TrueNAS access bootstrap failed: {type(exc).__name__}: {exc}",
            actual_tls_sha256=colonize(actual),
        )


if __name__ == "__main__":
    main()
