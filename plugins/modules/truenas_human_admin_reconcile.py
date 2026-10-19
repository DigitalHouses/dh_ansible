#!/usr/bin/python
# -*- coding: utf-8 -*-

from __future__ import annotations

DOCUMENTATION = r"""
---
module: truenas_human_admin_reconcile
short_description: Reconcile one TrueNAS human Web UI administrator
description:
  - Creates or repairs a declared local human administrator through pinned TLS JSON-RPC.
  - Uses the built-in local group carrying FULL_ADMIN instead of creating a custom privilege.
  - Never deletes users/groups and never changes conflicting UID ownership.
  - The declared password is create/recovery material, not rotated on an exact NOOP.
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
  desired_admin:
    type: dict
    required: true
  human_admin_password:
    type: str
    required: true
    no_log: true
  plan:
    type: dict
    required: true
  timeout:
    type: int
    default: 15
author:
  - DigitalHouses
"""

RETURN = r"""
action:
  type: str
local_admin_set_up:
  type: bool
password_login_verified:
  type: bool
tls_sha256_fingerprint:
  type: str
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
    payload = error.payload if isinstance(error.payload, dict) else {}
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    text = " ".join([
        str(payload.get("message") or ""),
        str(data.get("errname") or ""),
        str(data.get("reason") or ""),
    ]).lower()
    return "ebusy" in text or "rate limit" in text


def login(client, payload):
    for attempt in range(5):
        try:
            result = client.call("auth.login_ex", [payload])
            return isinstance(result, dict) and result.get("response_type") == "SUCCESS"
        except RpcError as exc:
            if not is_auth_rate_limit(exc) or attempt == 4:
                raise
            time.sleep(0.5 * (2 ** attempt))
    return False


def login_api_key(client, username, api_key):
    return login(client, {
        "mechanism": "API_KEY_PLAIN",
        "username": username,
        "api_key": api_key,
        "login_options": {"user_info": False},
    })


def login_password(client, username, password):
    return login(client, {
        "mechanism": "PASSWORD_PLAIN",
        "username": username,
        "password": password,
        "login_options": {"user_info": False},
    })


def query(client, method):
    result = client.call(method, [[], {}])
    return result if isinstance(result, list) else []


def group_ref_id(value):
    if isinstance(value, dict):
        return value.get("id")
    if isinstance(value, int):
        return value
    return None


def select_role_group(groups, role):
    candidates = [
        g for g in groups
        if g.get("local") is True
        and g.get("builtin") is True
        and role in (g.get("roles") or [])
    ]
    if len(candidates) != 1:
        raise RuntimeError(
            f"Expected exactly one built-in local group for role {role!r}, "
            f"found {len(candidates)}."
        )
    return candidates[0]


def assert_identity_safe(module, users, groups, desired, role_group):
    local_users = [
        u for u in users
        if u.get("local") is True and u.get("builtin") is not True
    ]
    same_name = [u for u in local_users if u.get("username") == desired["username"]]
    same_uid = [u for u in local_users if u.get("uid") == desired["uid"]]
    if len(same_name) > 1 or len(same_uid) > 1:
        module.fail_json(msg="Observed human administrator identity is not unique.")
    if same_name and same_name[0].get("uid") != desired["uid"]:
        module.fail_json(
            msg="Human administrator username exists with a different UID.",
            username=desired["username"],
            desired_uid=desired["uid"],
            observed_uid=same_name[0].get("uid"),
        )
    if same_uid and same_uid[0].get("username") != desired["username"]:
        module.fail_json(
            msg="Human administrator UID is owned by a different username.",
            uid=desired["uid"],
            observed_username=same_uid[0].get("username"),
        )

    user = same_name[0] if same_name else None
    if user:
        groups_by_id = {
            g.get("id"): g for g in groups if g.get("id") is not None
        }
        primary = groups_by_id.get(group_ref_id(user.get("group")), {})
        if not (
            primary.get("local") is True
            and primary.get("builtin") is not True
            and primary.get("name") == desired["username"]
        ):
            module.fail_json(
                msg="Human administrator private primary group topology is unsafe.",
                username=desired["username"],
            )
        if int(role_group["id"]) not in set(user.get("groups") or []):
            module.fail_json(
                msg="Human administrator is not a member of the expected built-in role group.",
                username=desired["username"],
            )
        if desired["role"] not in (user.get("roles") or []):
            module.fail_json(
                msg="Human administrator does not hold the declared role.",
                username=desired["username"],
                role=desired["role"],
            )
    return user


def verify_final(module, client, desired):
    groups = query(client, "group.query")
    users = query(client, "user.query")
    role_group = select_role_group(groups, desired["role"])
    user = assert_identity_safe(module, users, groups, desired, role_group)
    if user is None:
        module.fail_json(msg="Human administrator is absent after reconciliation.")

    if any([
        user.get("full_name") != desired["full_name"],
        bool(user.get("smb")) != desired["smb"],
        bool(user.get("password_disabled")) != desired["password_disabled"],
        bool(user.get("ssh_password_enabled")) != desired["ssh_password_enabled"],
        user.get("shell") != desired["shell"],
        user.get("home") != desired["home"],
        bool(user.get("locked")) != desired["locked"],
    ]):
        module.fail_json(
            msg="Human administrator properties do not match after reconciliation.",
            username=desired["username"],
        )

    local_admin_set_up = bool(client.call("user.has_local_administrator_set_up"))
    if not local_admin_set_up:
        module.fail_json(
            msg="TrueNAS still reports that no local administrator with a valid password exists."
        )
    return local_admin_set_up


def main():
    module = AnsibleModule(
        argument_spec=dict(
            host=dict(type="str", required=True),
            port=dict(type="int", default=443),
            endpoint=dict(type="str", default="/api/current"),
            tls_sha256_fingerprint=dict(type="str", required=True),
            username=dict(type="str", required=True),
            api_key=dict(type="str", required=True, no_log=True),
            desired_admin=dict(
                type="dict",
                required=True,
                options=dict(
                    username=dict(type="str", required=True),
                    uid=dict(type="int", required=True),
                    full_name=dict(type="str", required=True),
                    role=dict(type="str", required=True),
                    smb=dict(type="bool", required=True),
                    password_disabled=dict(type="bool", required=True),
                    ssh_password_enabled=dict(type="bool", required=True),
                    shell=dict(type="str", required=True),
                    home=dict(type="str", required=True),
                    locked=dict(type="bool", required=True),
                    password_secret=dict(type="str", required=True),
                ),
            ),
            human_admin_password=dict(type="str", required=True, no_log=True),
            plan=dict(type="dict", required=True),
            timeout=dict(type="int", default=15),
        ),
        supports_check_mode=False,
    )

    if connect is None:
        module.fail_json(msg="Python dependency 'websockets' is required.")

    desired = module.params["desired_admin"]
    password = module.params["human_admin_password"]
    plan = module.params["plan"]
    if plan.get("result") != "PASS":
        module.fail_json(msg="Human administrator apply requires a clean PASS plan.")
    action = (plan.get("admin") or {}).get("action")
    if action not in {"CREATE", "UPDATE", "RESET_PASSWORD", "NOOP"}:
        module.fail_json(msg="Human administrator plan contains an unsupported action.")
    if len(password) < 8:
        module.fail_json(msg="Human administrator recovery password must be at least 8 characters.")
    if not all([
        desired["role"] == "FULL_ADMIN",
        desired["smb"] is False,
        desired["password_disabled"] is False,
        desired["ssh_password_enabled"] is False,
        desired["shell"] == "/usr/sbin/nologin",
        desired["home"] == "/var/empty",
        desired["locked"] is False,
        desired["password_secret"] == "human_admin_password",
    ]):
        module.fail_json(msg="Human administrator desired security contract is invalid.")

    expected = normalize_fingerprint(module.params["tls_sha256_fingerprint"])
    try:
        actual = tls_peer_fingerprint(
            module.params["host"], module.params["port"], module.params["timeout"]
        )
    except Exception as exc:
        module.fail_json(msg=f"Unable to read TrueNAS TLS certificate: {type(exc).__name__}: {exc}")
    if actual != expected:
        module.fail_json(
            msg="TrueNAS TLS fingerprint mismatch. Authentication was not attempted.",
            expected_tls_sha256=colonize(expected),
            actual_tls_sha256=colonize(actual),
        )

    changed = False
    password_login_required = False

    try:
        with Client(
            module.params["host"],
            module.params["port"],
            module.params["endpoint"],
            module.params["timeout"],
        ) as client:
            if not login_api_key(
                client, module.params["username"], module.params["api_key"]
            ):
                module.fail_json(msg="TrueNAS write API authentication did not succeed.")

            groups = query(client, "group.query")
            users = query(client, "user.query")
            role_group = select_role_group(groups, desired["role"])

            if action == "CREATE":
                existing = assert_identity_safe(
                    module, users, groups, desired, role_group
                )
                private_groups = [
                    g for g in groups
                    if g.get("local") is True
                    and g.get("builtin") is not True
                    and g.get("name") == desired["username"]
                ]
                if existing is not None or private_groups:
                    module.fail_json(msg="Human administrator state changed after plan.")
                client.call("user.create", [{
                    "uid": desired["uid"],
                    "username": desired["username"],
                    "full_name": desired["full_name"],
                    "smb": False,
                    "group_create": True,
                    "groups": [int(role_group["id"])],
                    "home": desired["home"],
                    "home_create": False,
                    "shell": desired["shell"],
                    "password_disabled": False,
                    "ssh_password_enabled": False,
                    "locked": False,
                    "password": password,
                }])
                changed = True
                password_login_required = True

            elif action in {"UPDATE", "RESET_PASSWORD"}:
                existing = assert_identity_safe(
                    module, users, groups, desired, role_group
                )
                if existing is None:
                    module.fail_json(msg="Human administrator disappeared after plan.")

                if action == "UPDATE":
                    client.call("user.update", [existing["id"], {
                        "full_name": desired["full_name"],
                        "smb": False,
                        "password_disabled": False,
                        "ssh_password_enabled": False,
                        "shell": desired["shell"],
                        "home": desired["home"],
                        "locked": False,
                    }])
                    changed = True

                local_admin_set_up = bool(
                    client.call("user.has_local_administrator_set_up")
                )
                if action == "RESET_PASSWORD" or not local_admin_set_up:
                    client.call("user.set_password", [{
                        "username": desired["username"],
                        "old_password": None,
                        "new_password": password,
                    }])
                    changed = True
                    password_login_required = True

            local_admin_set_up = verify_final(module, client, desired)

        password_login_verified = False
        if password_login_required:
            with Client(
                module.params["host"],
                module.params["port"],
                module.params["endpoint"],
                module.params["timeout"],
            ) as client:
                password_login_verified = login_password(
                    client, desired["username"], password
                )
                if not password_login_verified:
                    module.fail_json(
                        msg="Human administrator password authentication verification failed."
                    )
                info = client.call("system.info")
                if not isinstance(info, dict) or not info.get("version"):
                    module.fail_json(
                        msg="Human administrator login succeeded but system.info verification failed."
                    )

    except RpcError as exc:
        module.fail_json(
            msg="TrueNAS human administrator JSON-RPC method failed.",
            rpc_error=safe_rpc_error(exc),
        )
    except Exception as exc:
        module.fail_json(
            msg=f"TrueNAS human administrator reconciliation failed: {type(exc).__name__}: {exc}"
        )

    module.exit_json(
        changed=changed,
        action=action,
        local_admin_set_up=local_admin_set_up,
        password_login_verified=password_login_verified,
        tls_sha256_fingerprint=colonize(actual),
    )


if __name__ == "__main__":
    main()
