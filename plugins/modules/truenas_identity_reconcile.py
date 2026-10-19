#!/usr/bin/python
# -*- coding: utf-8 -*-

from __future__ import annotations

DOCUMENTATION = r"""
---
module: truenas_identity_reconcile
short_description: Reconcile declared TrueNAS local groups and users
description:
  - Reconciles local access groups and users through the TrueNAS JSON-RPC API.
  - Verifies the pinned TLS fingerprint before authentication.
  - Resolves volatile TrueNAS group API identifiers from stable declared names and GIDs.
  - Never deletes principals and never changes declared UID/GID ownership conflicts.
  - Applies SMB passwords only when creating an SMB user.
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
  desired_groups:
    type: list
    elements: dict
    required: true
  desired_users:
    type: list
    elements: dict
    required: true
  smb_password:
    type: str
    required: false
    default: ""
    no_log: true
  timeout:
    type: int
    default: 15
author:
  - DigitalHouses
"""

RETURN = r"""
groups:
  type: list
users:
  type: list
tls_sha256_fingerprint:
  type: str
"""

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


def query(client, method):
    return client.call(method, [[], {}]) or []


def local_mutable(items):
    return [
        item for item in items
        if item.get("local") is True and item.get("builtin") is not True
    ]


def group_ref_id(value):
    if isinstance(value, dict):
        return value.get("id")
    if isinstance(value, int):
        return value
    return None


def require_unique_declared(module, desired_groups, desired_users):
    group_names = [g["name"] for g in desired_groups]
    group_gids = [g["gid"] for g in desired_groups]
    usernames = [u["username"] for u in desired_users]
    uids = [u["uid"] for u in desired_users]

    if len(group_names) != len(set(group_names)):
        module.fail_json(msg="Declared TrueNAS group names must be unique.")
    if len(group_gids) != len(set(group_gids)):
        module.fail_json(msg="Declared TrueNAS group GIDs must be unique.")
    if len(usernames) != len(set(usernames)):
        module.fail_json(msg="Declared TrueNAS usernames must be unique.")
    if len(uids) != len(set(uids)):
        module.fail_json(msg="Declared TrueNAS user UIDs must be unique.")

    declared_groups = set(group_names)
    for user in desired_users:
        refs = [user["primary_group"]] + list(user.get("supplementary_groups") or [])
        unknown = [name for name in refs if name not in declared_groups]
        if unknown:
            module.fail_json(
                msg="Declared TrueNAS user references undeclared groups.",
                username=user["username"],
                unknown_groups=unknown,
            )


def assert_group_identity_safe(module, observed_groups, desired):
    same_name = [g for g in observed_groups if g.get("name") == desired["name"]]
    same_gid = [g for g in observed_groups if g.get("gid") == desired["gid"]]

    if len(same_name) > 1 or len(same_gid) > 1:
        module.fail_json(
            msg="Observed TrueNAS group identity is not unique.",
            group=desired["name"],
            gid=desired["gid"],
        )

    if same_name and same_name[0].get("gid") != desired["gid"]:
        module.fail_json(
            msg="TrueNAS group name exists with a different GID.",
            group=desired["name"],
            desired_gid=desired["gid"],
            observed_gid=same_name[0].get("gid"),
        )

    if same_gid and same_gid[0].get("name") != desired["name"]:
        module.fail_json(
            msg="TrueNAS GID is owned by a different group.",
            group=desired["name"],
            gid=desired["gid"],
            observed_name=same_gid[0].get("name"),
        )

    return same_name[0] if same_name else None


def assert_user_identity_safe(module, observed_users, desired):
    same_name = [u for u in observed_users if u.get("username") == desired["username"]]
    same_uid = [u for u in observed_users if u.get("uid") == desired["uid"]]

    if len(same_name) > 1 or len(same_uid) > 1:
        module.fail_json(
            msg="Observed TrueNAS user identity is not unique.",
            username=desired["username"],
            uid=desired["uid"],
        )

    if same_name and same_name[0].get("uid") != desired["uid"]:
        module.fail_json(
            msg="TrueNAS username exists with a different UID.",
            username=desired["username"],
            desired_uid=desired["uid"],
            observed_uid=same_name[0].get("uid"),
        )

    if same_uid and same_uid[0].get("username") != desired["username"]:
        module.fail_json(
            msg="TrueNAS UID is owned by a different username.",
            username=desired["username"],
            uid=desired["uid"],
            observed_username=same_uid[0].get("username"),
        )

    return same_name[0] if same_name else None


def user_membership_names(user, groups_by_id):
    primary = groups_by_id.get(group_ref_id(user.get("group")), {})
    primary_name = primary.get("name")

    extras = []
    for group_id in user.get("groups") or []:
        group = groups_by_id.get(group_id)
        if not group:
            continue
        if group.get("builtin") is True or group.get("local") is not True:
            continue
        extras.append(group.get("name"))

    return primary_name, sorted(set(extras))


def main():
    module = AnsibleModule(
        argument_spec=dict(
            host=dict(type="str", required=True),
            port=dict(type="int", default=443),
            endpoint=dict(type="str", default="/api/current"),
            tls_sha256_fingerprint=dict(type="str", required=True),
            username=dict(type="str", required=True),
            api_key=dict(type="str", required=True, no_log=True),
            desired_groups=dict(
                type="list",
                elements="dict",
                required=True,
                options=dict(
                    name=dict(type="str", required=True),
                    gid=dict(type="int", required=True),
                    smb=dict(type="bool", default=False),
                ),
            ),
            desired_users=dict(
                type="list",
                elements="dict",
                required=True,
                options=dict(
                    username=dict(type="str", required=True),
                    uid=dict(type="int", required=True),
                    full_name=dict(type="str", required=True),
                    primary_group=dict(type="str", required=True),
                    supplementary_groups=dict(type="list", elements="str", default=[]),
                    smb=dict(type="bool", default=False),
                    password_disabled=dict(type="bool", default=True),
                    ssh_password_enabled=dict(type="bool", default=False),
                    shell=dict(type="str", default="/usr/sbin/nologin"),
                    home=dict(type="str", default="/var/empty"),
                    password_secret=dict(type="str", required=False),
                ),
            ),
            smb_password=dict(type="str", default="", no_log=True),
            timeout=dict(type="int", default=15),
        ),
        supports_check_mode=True,
    )

    if connect is None:
        module.fail_json(msg="Python dependency 'websockets' is required.")

    desired_groups = module.params["desired_groups"]
    desired_users = module.params["desired_users"]
    require_unique_declared(module, desired_groups, desired_users)

    for user in desired_users:
        if user["smb"]:
            if user["password_disabled"]:
                module.fail_json(
                    msg="SMB users cannot have password_disabled=true.",
                    username=user["username"],
                )
            if user.get("password_secret") != "smb_password":
                module.fail_json(
                    msg="SMB users must declare password_secret=smb_password.",
                    username=user["username"],
                )
            if len(module.params["smb_password"]) < 8:
                module.fail_json(
                    msg="Fleet Vault SMB password must contain at least 8 characters."
                )
        elif user.get("password_secret"):
            module.fail_json(
                msg="Non-SMB service users must not declare a password secret.",
                username=user["username"],
            )

    expected = normalize_fingerprint(module.params["tls_sha256_fingerprint"])
    try:
        actual = tls_peer_fingerprint(
            module.params["host"],
            module.params["port"],
            module.params["timeout"],
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
    group_results = []
    user_results = []

    try:
        with Client(
            module.params["host"],
            module.params["port"],
            module.params["endpoint"],
            module.params["timeout"],
        ) as client:
            auth = client.call(
                "auth.login_ex",
                [{
                    "mechanism": "API_KEY_PLAIN",
                    "username": module.params["username"],
                    "api_key": module.params["api_key"],
                    "login_options": {"user_info": False},
                }],
            )
            if not isinstance(auth, dict) or auth.get("response_type") != "SUCCESS":
                module.fail_json(msg="TrueNAS API authentication did not succeed.")

            observed_groups_all = query(client, "group.query")
            observed_users_all = query(client, "user.query")
            observed_groups = local_mutable(observed_groups_all)
            observed_users = local_mutable(observed_users_all)

            # Preflight every declared numeric/name identity before the first write.
            for desired in desired_groups:
                assert_group_identity_safe(module, observed_groups, desired)
            for desired in desired_users:
                assert_user_identity_safe(module, observed_users, desired)

            # Groups first. Their TrueNAS API IDs are intentionally discovered,
            # never declared in inventory.
            for desired in desired_groups:
                observed = assert_group_identity_safe(module, observed_groups, desired)
                action = "NOOP"

                if observed is None:
                    action = "CREATE"
                    if not module.check_mode:
                        client.call("group.create", [{
                            "gid": desired["gid"],
                            "name": desired["name"],
                            "smb": desired["smb"],
                            "sudo_commands": [],
                            "sudo_commands_nopasswd": [],
                        }])
                    changed = True
                elif bool(observed.get("smb")) != bool(desired["smb"]):
                    action = "UPDATE"
                    if not module.check_mode:
                        client.call(
                            "group.update",
                            [observed["id"], {"smb": desired["smb"]}],
                        )
                    changed = True

                group_results.append({
                    "name": desired["name"],
                    "gid": desired["gid"],
                    "action": action,
                })

            if module.check_mode and changed:
                module.exit_json(
                    changed=True,
                    groups=group_results,
                    users=[],
                    tls_sha256_fingerprint=colonize(actual),
                    check_mode_replan_required=True,
                )

            # Refresh group API IDs after any create/update.
            observed_groups_all = query(client, "group.query")
            groups_by_name = {
                group["name"]: group
                for group in local_mutable(observed_groups_all)
            }
            groups_by_id = {
                group.get("id"): group
                for group in observed_groups_all
                if group.get("id") is not None
            }

            for desired in desired_groups:
                group = groups_by_name.get(desired["name"])
                if (
                    not group
                    or group.get("gid") != desired["gid"]
                    or bool(group.get("smb")) != bool(desired["smb"])
                ):
                    module.fail_json(
                        msg="TrueNAS group acceptance failed after reconciliation.",
                        group=desired["name"],
                    )

            # Refresh users immediately before user writes.
            observed_users = local_mutable(query(client, "user.query"))

            for desired in desired_users:
                observed = assert_user_identity_safe(module, observed_users, desired)
                primary_id = groups_by_name[desired["primary_group"]]["id"]
                extra_ids = [
                    groups_by_name[name]["id"]
                    for name in desired.get("supplementary_groups") or []
                ]
                action = "NOOP"

                if observed is None:
                    action = "CREATE"
                    payload = {
                        "uid": desired["uid"],
                        "username": desired["username"],
                        "full_name": desired["full_name"],
                        "group": primary_id,
                        "groups": extra_ids,
                        "group_create": False,
                        "home": desired["home"],
                        "home_create": False,
                        "shell": desired["shell"],
                        "smb": desired["smb"],
                        "password_disabled": desired["password_disabled"],
                        "ssh_password_enabled": desired["ssh_password_enabled"],
                        "password": (
                            module.params["smb_password"]
                            if desired["smb"]
                            else None
                        ),
                    }
                    if not module.check_mode:
                        client.call("user.create", [payload])
                    changed = True
                else:
                    primary_name, extra_names = user_membership_names(
                        observed, groups_by_id
                    )
                    desired_extras = sorted(desired.get("supplementary_groups") or [])

                    drift = any([
                        observed.get("full_name") != desired["full_name"],
                        bool(observed.get("smb")) != bool(desired["smb"]),
                        bool(observed.get("password_disabled")) != bool(desired["password_disabled"]),
                        bool(observed.get("ssh_password_enabled")) != bool(desired["ssh_password_enabled"]),
                        observed.get("shell") != desired["shell"],
                        observed.get("home") != desired["home"],
                        primary_name != desired["primary_group"],
                        extra_names != desired_extras,
                    ])

                    if observed.get("roles") or []:
                        module.fail_json(
                            msg="Refusing to modify a declared access user with admin roles.",
                            username=desired["username"],
                        )

                    if drift:
                        action = "UPDATE"
                        payload = {
                            "full_name": desired["full_name"],
                            "group": primary_id,
                            "groups": extra_ids,
                            "home": desired["home"],
                            "shell": desired["shell"],
                            "smb": desired["smb"],
                            "password_disabled": desired["password_disabled"],
                            "ssh_password_enabled": desired["ssh_password_enabled"],
                        }
                        # Password rotation is deliberately not implicit.
                        if not module.check_mode:
                            client.call("user.update", [observed["id"], payload])
                        changed = True

                user_results.append({
                    "username": desired["username"],
                    "uid": desired["uid"],
                    "action": action,
                })

            if module.check_mode:
                module.exit_json(
                    changed=changed,
                    groups=group_results,
                    users=user_results,
                    tls_sha256_fingerprint=colonize(actual),
                )

            # Final exact acceptance.
            final_groups_all = query(client, "group.query")
            final_users = local_mutable(query(client, "user.query"))
            final_groups = local_mutable(final_groups_all)
            final_groups_by_name = {g["name"]: g for g in final_groups}
            final_groups_by_id = {
                g.get("id"): g for g in final_groups_all if g.get("id") is not None
            }
            final_users_by_name = {u["username"]: u for u in final_users}

            for desired in desired_groups:
                group = final_groups_by_name.get(desired["name"])
                if not group or group.get("gid") != desired["gid"] or bool(group.get("smb")) != bool(desired["smb"]):
                    module.fail_json(
                        msg="Final TrueNAS group verification failed.",
                        group=desired["name"],
                    )

            for desired in desired_users:
                user = final_users_by_name.get(desired["username"])
                if not user or user.get("uid") != desired["uid"]:
                    module.fail_json(
                        msg="Final TrueNAS user identity verification failed.",
                        username=desired["username"],
                    )
                primary_name, extra_names = user_membership_names(
                    user, final_groups_by_id
                )
                expected_extras = sorted(desired.get("supplementary_groups") or [])
                if any([
                    user.get("full_name") != desired["full_name"],
                    bool(user.get("smb")) != bool(desired["smb"]),
                    bool(user.get("password_disabled")) != bool(desired["password_disabled"]),
                    bool(user.get("ssh_password_enabled")) != bool(desired["ssh_password_enabled"]),
                    user.get("shell") != desired["shell"],
                    user.get("home") != desired["home"],
                    primary_name != desired["primary_group"],
                    extra_names != expected_extras,
                    bool(user.get("roles")),
                ]):
                    module.fail_json(
                        msg="Final TrueNAS user property verification failed.",
                        username=desired["username"],
                    )

    except RpcError as exc:
        module.fail_json(
            msg="TrueNAS identity JSON-RPC method failed.",
            rpc_error=safe_rpc_error(exc),
        )
    except Exception as exc:
        module.fail_json(
            msg=f"TrueNAS identity reconciliation failed: {type(exc).__name__}: {exc}"
        )

    module.exit_json(
        changed=changed,
        groups=group_results,
        users=user_results,
        tls_sha256_fingerprint=colonize(actual),
    )


if __name__ == "__main__":
    main()
