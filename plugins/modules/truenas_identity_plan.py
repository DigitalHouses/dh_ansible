#!/usr/bin/python
# -*- coding: utf-8 -*-

from __future__ import annotations

DOCUMENTATION = r"""
---
module: truenas_identity_plan
short_description: Build a read-only TrueNAS local identity reconciliation plan
description:
  - Compares declarative local group and user targets with sanitized TrueNAS query results.
  - Performs no network calls and no writes.
  - Resolves volatile TrueNAS API group IDs to stable group names for membership comparison.
options:
  observed_groups:
    type: list
    elements: dict
    required: true
  observed_users:
    type: list
    elements: dict
    required: true
  desired_groups:
    type: list
    elements: dict
    required: true
  desired_users:
    type: list
    elements: dict
    required: true
author:
  - DigitalHouses
"""

EXAMPLES = r"""
- name: Plan TrueNAS identities
  truenas_identity_plan:
    observed_groups: "{{ truenas_identity_observed.results.groups }}"
    observed_users: "{{ truenas_identity_observed.results.users }}"
    desired_groups: "{{ truenas_provision.identity.groups }}"
    desired_users: "{{ truenas_provision.identity.users }}"
"""

RETURN = r"""
result:
  type: str
groups:
  type: list
users:
  type: list
gates:
  type: dict
"""

from ansible.module_utils.basic import AnsibleModule


def local_mutable_groups(groups):
    return [
        g for g in groups
        if g.get("local") is True and g.get("builtin") is not True
    ]


def local_mutable_users(users):
    return [
        u for u in users
        if u.get("local") is True and u.get("builtin") is not True
    ]


def group_ref_id(value):
    if isinstance(value, dict):
        return value.get("id")
    if isinstance(value, int):
        return value
    return None


def unique(values):
    return len(values) == len(set(values))


def main():
    module = AnsibleModule(
        argument_spec=dict(
            observed_groups=dict(type="list", elements="dict", required=True),
            observed_users=dict(type="list", elements="dict", required=True),
            desired_groups=dict(type="list", elements="dict", required=True),
            desired_users=dict(type="list", elements="dict", required=True),
        ),
        supports_check_mode=True,
    )

    observed_groups_all = module.params["observed_groups"]
    observed_users_all = module.params["observed_users"]
    desired_groups = module.params["desired_groups"]
    desired_users = module.params["desired_users"]

    observed_groups = local_mutable_groups(observed_groups_all)
    observed_users = local_mutable_users(observed_users_all)

    desired_group_names = [g.get("name") for g in desired_groups]
    desired_group_gids = [g.get("gid") for g in desired_groups]
    desired_user_names = [u.get("username") for u in desired_users]
    desired_user_uids = [u.get("uid") for u in desired_users]

    declared_group_names = set(desired_group_names)

    references_valid = True
    for user in desired_users:
        primary = user.get("primary_group")
        extras = user.get("supplementary_groups") or []
        if primary not in declared_group_names:
            references_valid = False
        if any(name not in declared_group_names for name in extras):
            references_valid = False

    desired_no_admin_roles = all(not (u.get("roles") or []) for u in desired_users)

    group_plan = []
    group_conflicts = False
    for desired in desired_groups:
        name = desired.get("name")
        gid = desired.get("gid")
        same_name = [g for g in observed_groups if g.get("name") == name]
        same_gid = [g for g in observed_groups if g.get("gid") == gid]

        if len(same_name) > 1 or len(same_gid) > 1:
            action = "BLOCK"
            reason = "non_unique_observed_group_identity"
            group_conflicts = True
            observed = same_name[0] if same_name else (same_gid[0] if same_gid else None)
        elif same_name:
            observed = same_name[0]
            if observed.get("gid") != gid:
                action = "BLOCK"
                reason = "group_name_gid_conflict"
                group_conflicts = True
            elif same_gid and same_gid[0].get("name") != name:
                action = "BLOCK"
                reason = "group_gid_name_conflict"
                group_conflicts = True
            elif bool(observed.get("smb")) != bool(desired.get("smb")):
                action = "UPDATE"
                reason = "group_properties_drift"
            elif observed.get("roles") or []:
                action = "BLOCK"
                reason = "unexpected_group_admin_roles"
                group_conflicts = True
            else:
                action = "NOOP"
                reason = "group_matches"
        elif same_gid:
            observed = same_gid[0]
            action = "BLOCK"
            reason = "group_gid_owned_by_other_name"
            group_conflicts = True
        else:
            observed = None
            action = "CREATE"
            reason = "group_absent"

        group_plan.append({
            "name": name,
            "gid": gid,
            "smb": bool(desired.get("smb")),
            "action": action,
            "reason": reason,
            "observed": (
                {
                    "id": observed.get("id"),
                    "name": observed.get("name"),
                    "gid": observed.get("gid"),
                    "smb": observed.get("smb"),
                    "roles": observed.get("roles") or [],
                }
                if observed
                else None
            ),
        })

    all_group_by_id = {
        g.get("id"): g
        for g in observed_groups_all
        if g.get("id") is not None
    }

    user_plan = []
    user_conflicts = False
    for desired in desired_users:
        username = desired.get("username")
        uid = desired.get("uid")
        same_name = [u for u in observed_users if u.get("username") == username]
        same_uid = [u for u in observed_users if u.get("uid") == uid]

        observed = same_name[0] if len(same_name) == 1 else None
        reason = "user_absent"
        action = "CREATE"

        if len(same_name) > 1 or len(same_uid) > 1:
            action = "BLOCK"
            reason = "non_unique_observed_user_identity"
            user_conflicts = True
            observed = same_name[0] if same_name else (same_uid[0] if same_uid else None)
        elif same_name:
            observed = same_name[0]
            if observed.get("uid") != uid:
                action = "BLOCK"
                reason = "username_uid_conflict"
                user_conflicts = True
            elif same_uid and same_uid[0].get("username") != username:
                action = "BLOCK"
                reason = "uid_username_conflict"
                user_conflicts = True
            else:
                primary_id = group_ref_id(observed.get("group"))
                primary_group = all_group_by_id.get(primary_id, {})
                primary_name = primary_group.get("name")

                extra_names = []
                for group_id in observed.get("groups") or []:
                    group = all_group_by_id.get(group_id)
                    if not group or group.get("builtin") is True:
                        continue
                    if group.get("local") is not True:
                        continue
                    extra_names.append(group.get("name"))

                desired_extras = sorted(desired.get("supplementary_groups") or [])
                observed_extras = sorted(set(extra_names))

                property_drift = any([
                    observed.get("full_name") != desired.get("full_name"),
                    bool(observed.get("smb")) != bool(desired.get("smb")),
                    bool(observed.get("password_disabled")) != bool(desired.get("password_disabled")),
                    bool(observed.get("ssh_password_enabled")) != bool(desired.get("ssh_password_enabled")),
                    observed.get("shell") != desired.get("shell"),
                    observed.get("home") != desired.get("home"),
                    primary_name != desired.get("primary_group"),
                    observed_extras != desired_extras,
                ])

                if observed.get("roles") or []:
                    action = "BLOCK"
                    reason = "unexpected_user_admin_roles"
                    user_conflicts = True
                elif property_drift:
                    action = "UPDATE"
                    reason = "user_properties_or_membership_drift"
                else:
                    action = "NOOP"
                    reason = "user_matches"
        elif same_uid:
            observed = same_uid[0]
            action = "BLOCK"
            reason = "uid_owned_by_other_username"
            user_conflicts = True

        user_plan.append({
            "username": username,
            "uid": uid,
            "primary_group": desired.get("primary_group"),
            "supplementary_groups": desired.get("supplementary_groups") or [],
            "smb": bool(desired.get("smb")),
            "password_disabled": bool(desired.get("password_disabled")),
            "ssh_password_enabled": bool(desired.get("ssh_password_enabled")),
            "shell": desired.get("shell"),
            "home": desired.get("home"),
            "action": action,
            "reason": reason,
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
                    "roles": observed.get("roles") or [],
                }
                if observed
                else None
            ),
        })

    gates = {
        "desired_group_names_unique": unique(desired_group_names),
        "desired_group_gids_unique": unique(desired_group_gids),
        "desired_usernames_unique": unique(desired_user_names),
        "desired_uids_unique": unique(desired_user_uids),
        "desired_group_references_valid": references_valid,
        "desired_users_no_admin_roles": desired_no_admin_roles,
        "no_group_identity_conflicts": not group_conflicts,
        "no_user_identity_conflicts": not user_conflicts,
    }

    result = "PASS" if all(gates.values()) else "BLOCK"

    module.exit_json(
        changed=False,
        result=result,
        gates=gates,
        groups=group_plan,
        users=user_plan,
    )


if __name__ == "__main__":
    main()
