#!/usr/bin/python
# -*- coding: utf-8 -*-

from __future__ import annotations

DOCUMENTATION = r"""
---
module: truenas_human_admin_plan
short_description: Build a read-only TrueNAS human administrator plan
description:
  - Compares one declarative human Web UI administrator with TrueNAS users/groups.
  - Requires the built-in local group carrying the declared administrator role.
  - Never performs network calls or writes.
options:
  observed_groups:
    type: list
    elements: dict
    required: true
  observed_users:
    type: list
    elements: dict
    required: true
  local_admin_set_up:
    type: bool
    required: true
  desired_admin:
    type: dict
    required: true
  force_password_rotation:
    type: bool
    default: false
author:
  - DigitalHouses
"""

RETURN = r"""
result:
  type: str
admin:
  type: dict
gates:
  type: dict
"""

from ansible.module_utils.basic import AnsibleModule


def group_ref_id(value):
    if isinstance(value, dict):
        return value.get("id")
    if isinstance(value, int):
        return value
    return None


def main():
    module = AnsibleModule(
        argument_spec=dict(
            observed_groups=dict(type="list", elements="dict", required=True),
            observed_users=dict(type="list", elements="dict", required=True),
            local_admin_set_up=dict(type="bool", required=True),
            force_password_rotation=dict(type="bool", default=False, no_log=False),
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
        ),
        supports_check_mode=True,
    )

    groups = module.params["observed_groups"]
    users = module.params["observed_users"]
    desired = module.params["desired_admin"]
    local_admin_set_up = bool(module.params["local_admin_set_up"])
    force_password_rotation = bool(module.params["force_password_rotation"])

    role_groups = [
        g for g in groups
        if g.get("local") is True
        and g.get("builtin") is True
        and desired["role"] in (g.get("roles") or [])
    ]
    local_users = [
        u for u in users
        if u.get("local") is True and u.get("builtin") is not True
    ]
    same_name = [u for u in local_users if u.get("username") == desired["username"]]
    same_uid = [u for u in local_users if u.get("uid") == desired["uid"]]
    private_groups = [
        g for g in groups
        if g.get("local") is True
        and g.get("builtin") is not True
        and g.get("name") == desired["username"]
    ]

    security_contract = all([
        desired["role"] == "FULL_ADMIN",
        desired["smb"] is False,
        desired["password_disabled"] is False,
        desired["ssh_password_enabled"] is False,
        desired["shell"] == "/usr/sbin/nologin",
        desired["home"] == "/var/empty",
        desired["locked"] is False,
        desired["password_secret"] == "human_admin_password",
    ])

    gates = {
        "desired_security_contract": security_contract,
        "builtin_role_group_unique": len(role_groups) == 1,
        "observed_username_unique": len(same_name) <= 1,
        "observed_uid_unique": len(same_uid) <= 1,
        "private_group_unique": len(private_groups) <= 1,
    }

    action = "BLOCK"
    reason = "plan_gate_failed"
    observed = same_name[0] if len(same_name) == 1 else None

    if all(gates.values()):
        if observed is None:
            if same_uid:
                reason = "uid_owned_by_other_username"
            elif private_groups:
                reason = "private_group_exists_without_declared_user"
            else:
                action = "CREATE"
                reason = "human_admin_absent"
        else:
            if observed.get("uid") != desired["uid"]:
                reason = "username_uid_conflict"
            elif same_uid and same_uid[0].get("username") != desired["username"]:
                reason = "uid_username_conflict"
            else:
                groups_by_id = {
                    g.get("id"): g for g in groups if g.get("id") is not None
                }
                primary_group = groups_by_id.get(
                    group_ref_id(observed.get("group")), {}
                )
                role_group = role_groups[0]
                extra_ids = set(observed.get("groups") or [])
                role_membership_ok = int(role_group["id"]) in extra_ids
                private_primary_ok = (
                    primary_group.get("local") is True
                    and primary_group.get("builtin") is not True
                    and primary_group.get("name") == desired["username"]
                )
                role_ok = desired["role"] in (observed.get("roles") or [])
                privilege_topology_ok = (
                    role_membership_ok and private_primary_ok and role_ok
                )

                property_drift = any([
                    observed.get("full_name") != desired["full_name"],
                    bool(observed.get("smb")) != desired["smb"],
                    bool(observed.get("password_disabled")) != desired["password_disabled"],
                    bool(observed.get("ssh_password_enabled")) != desired["ssh_password_enabled"],
                    observed.get("shell") != desired["shell"],
                    observed.get("home") != desired["home"],
                    bool(observed.get("locked")) != desired["locked"],
                ])

                if not privilege_topology_ok:
                    reason = "human_admin_privilege_topology_drift"
                elif property_drift:
                    action = "UPDATE"
                    reason = "human_admin_properties_drift"
                elif not local_admin_set_up:
                    action = "RESET_PASSWORD"
                    reason = "no_local_admin_with_valid_password"
                elif force_password_rotation:
                    action = "RESET_PASSWORD"
                    reason = "explicit_fleet_password_rotation"
                else:
                    action = "NOOP"
                    reason = "human_admin_matches"

    result = "PASS" if action != "BLOCK" else "BLOCK"
    module.exit_json(
        changed=False,
        result=result,
        gates=gates,
        admin={
            "username": desired["username"],
            "uid": desired["uid"],
            "role": desired["role"],
            "action": action,
            "reason": reason,
            "local_admin_set_up": local_admin_set_up,
            "observed": (
                {
                    "id": observed.get("id"),
                    "username": observed.get("username"),
                    "uid": observed.get("uid"),
                    "full_name": observed.get("full_name"),
                    "smb": observed.get("smb"),
                    "password_disabled": observed.get("password_disabled"),
                    "ssh_password_enabled": observed.get("ssh_password_enabled"),
                    "shell": observed.get("shell"),
                    "home": observed.get("home"),
                    "locked": observed.get("locked"),
                    "roles": observed.get("roles") or [],
                }
                if observed else None
            ),
        },
    )


if __name__ == "__main__":
    main()
