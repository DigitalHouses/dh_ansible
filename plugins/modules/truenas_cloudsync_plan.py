#!/usr/bin/python
# -*- coding: utf-8 -*-

from __future__ import annotations

DOCUMENTATION = r"""
---
module: truenas_cloudsync_plan
short_description: Build a read-only TrueNAS Cloud Sync reconciliation plan
description:
  - Compares sanitized live Cloud Sync state with declarative backup targets.
  - Performs no network calls and no writes.
  - Blocks source, destination, credential, encryption, or data-scope drift for SYNC tasks.
options:
  observed_credentials:
    type: list
    elements: dict
    required: true
  observed_tasks:
    type: list
    elements: dict
    required: true
  desired_credential:
    type: dict
    required: true
  desired_tasks:
    type: list
    elements: dict
    required: true
  pool_name:
    type: str
    required: true
author:
  - DigitalHouses
"""

from ansible.module_utils.basic import AnsibleModule


def unique(values):
    return len(values) == len(set(values))


def safe_relative_path(value):
    if not isinstance(value, str) or not value or value.startswith("/"):
        return False
    parts = value.split("/")
    return all(part not in ("", ".", "..") for part in parts)


def normalize_folder(value):
    if not isinstance(value, str):
        return value
    if value in ("", "/"):
        return "/"
    return "/" + value.strip("/")


def normalized_schedule(value):
    value = value if isinstance(value, dict) else {}
    return {
        "minute": str(value.get("minute", "00")),
        "hour": str(value.get("hour", "*")),
        "dom": str(value.get("dom", "*")),
        "month": str(value.get("month", "*")),
        "dow": str(value.get("dow", "*")),
    }


def desired_task(pool_name, credential, item):
    relative = item["relative_path"]
    return {
        "description": item["description"],
        "path": f"/mnt/{pool_name}/{relative}",
        "credential_name": credential["name"],
        "credential_provider_type": credential["provider_type"],
        "attributes": {
            "bucket": item["bucket"],
            "folder": normalize_folder(item["folder"]),
            "fast_list": bool(item.get("fast_list", False)),
        },
        "schedule": normalized_schedule(item.get("schedule")),
        "pre_script_present": False,
        "post_script_present": False,
        "args_present": False,
        "locked": False,
        "snapshot": bool(item.get("snapshot", False)),
        "include": item.get("include") or [],
        "exclude": item.get("exclude") or [],
        "enabled": bool(item.get("enabled", True)),
        "adopt_existing_remote_prefix": bool(item.get("adopt_existing_remote_prefix", False)),
        "bwlimit": item.get("bwlimit") or [],
        "transfers": item.get("transfers"),
        "direction": item["direction"],
        "transfer_mode": item["transfer_mode"],
        "encryption": bool(item.get("encryption", False)),
        "filename_encryption": bool(item.get("filename_encryption", False)),
        "create_empty_src_dirs": bool(item.get("create_empty_src_dirs", False)),
        "follow_symlinks": bool(item.get("follow_symlinks", False)),
    }


def managed_projection(item):
    attributes = item.get("attributes") if isinstance(item.get("attributes"), dict) else {}
    return {
        "id": item.get("id"),
        "description": item.get("description"),
        "path": item.get("path"),
        "credential_name": item.get("credential_name"),
        "credential_provider_type": item.get("credential_provider_type"),
        "attributes": {
            "bucket": attributes.get("bucket"),
            "folder": normalize_folder(attributes.get("folder")),
            "fast_list": bool(attributes.get("fast_list", False)),
        },
        "schedule": normalized_schedule(item.get("schedule")),
        "pre_script_present": bool(item.get("pre_script_present", False)),
        "post_script_present": bool(item.get("post_script_present", False)),
        "args_present": bool(item.get("args_present", False)),
        "locked": bool(item.get("locked", False)),
        "snapshot": bool(item.get("snapshot", False)),
        "include": item.get("include") or [],
        "exclude": item.get("exclude") or [],
        "enabled": bool(item.get("enabled", False)),
        "bwlimit": item.get("bwlimit") or [],
        "transfers": item.get("transfers"),
        "direction": item.get("direction"),
        "transfer_mode": item.get("transfer_mode"),
        "encryption": bool(item.get("encryption", False)),
        "filename_encryption": bool(item.get("filename_encryption", False)),
        "create_empty_src_dirs": bool(item.get("create_empty_src_dirs", False)),
        "follow_symlinks": bool(item.get("follow_symlinks", False)),
    }


IDENTITY_FIELDS = (
    "path",
    "credential_name",
    "credential_provider_type",
    "direction",
    "transfer_mode",
    "encryption",
    "filename_encryption",
    "include",
    "exclude",
    "create_empty_src_dirs",
    "follow_symlinks",
    "pre_script_present",
    "post_script_present",
    "args_present",
    "locked",
)

MUTABLE_FIELDS = (
    "schedule",
    "snapshot",
    "enabled",
    "bwlimit",
    "transfers",
)


def main():
    module = AnsibleModule(
        argument_spec=dict(
            observed_credentials=dict(type="list", elements="dict", required=True),
            observed_tasks=dict(type="list", elements="dict", required=True),
            desired_credential=dict(type="dict", required=True),
            desired_tasks=dict(type="list", elements="dict", required=True),
            pool_name=dict(type="str", required=True),
        ),
        supports_check_mode=True,
    )

    observed_credentials = module.params["observed_credentials"]
    observed_tasks = module.params["observed_tasks"]
    credential = module.params["desired_credential"]
    desired_items = module.params["desired_tasks"]
    pool_name = module.params["pool_name"]

    credential_name = credential.get("name")
    provider_type = credential.get("provider_type")

    descriptions = [item.get("description") for item in desired_items]
    relative_paths = [item.get("relative_path") for item in desired_items]
    destinations = [
        (item.get("bucket"), normalize_folder(item.get("folder")))
        for item in desired_items
    ]

    gates = {
        "pool_name_safe": (
            isinstance(pool_name, str)
            and bool(pool_name)
            and "/" not in pool_name
            and pool_name not in (".", "..")
        ),
        "credential_declared": (
            isinstance(credential_name, str)
            and bool(credential_name)
            and provider_type == "B2"
        ),
        "task_descriptions_unique": unique(descriptions),
        "task_relative_paths_unique": unique(relative_paths),
        "task_destinations_unique": unique(destinations),
        "task_paths_safe": all(safe_relative_path(path) for path in relative_paths),
        "push_sync_only": all(
            item.get("direction") == "PUSH"
            and item.get("transfer_mode") == "SYNC"
            for item in desired_items
        ),
        "client_side_encryption_consistent": all(
            bool(item.get("filename_encryption", False))
            <= bool(item.get("encryption", False))
            for item in desired_items
        ),
        "remote_prefix_adoption_policy_boolean": all(
            isinstance(item.get("adopt_existing_remote_prefix", False), bool)
            for item in desired_items
        ),
    }

    issues = []
    for key, passed in gates.items():
        if not passed:
            issues.append(f"gate_failed:{key}")

    credential_matches = [
        item for item in observed_credentials
        if item.get("name") == credential_name
    ]
    other_b2_credentials = [
        item for item in observed_credentials
        if item.get("provider_type") == provider_type
        and item.get("name") != credential_name
    ]
    credential_action = "CREATE"
    credential_reason = "credential_absent"
    credential_observed = None

    if not credential_matches and other_b2_credentials:
        credential_action = "BLOCK"
        credential_reason = "existing_b2_credential_requires_adoption"
        issues.append("existing_b2_credential_requires_adoption")
        credential_observed = other_b2_credentials[0]
    elif len(credential_matches) > 1:
        credential_action = "BLOCK"
        credential_reason = "credential_name_not_unique"
        issues.append("credential_name_not_unique")
        credential_observed = credential_matches[0]
    elif len(credential_matches) == 1:
        credential_observed = credential_matches[0]
        if credential_observed.get("provider_type") != provider_type:
            credential_action = "BLOCK"
            credential_reason = "credential_provider_conflict"
            issues.append("credential_provider_conflict")
        else:
            credential_action = "NOOP"
            credential_reason = "credential_matches"

    desired_resolved = [
        desired_task(pool_name, credential, item)
        for item in desired_items
        if safe_relative_path(item.get("relative_path")) and gates["pool_name_safe"]
    ]

    task_plan = []
    for desired in desired_resolved:
        same_description = [
            item for item in observed_tasks
            if item.get("description") == desired["description"]
        ]
        same_path_other = [
            item for item in observed_tasks
            if item.get("path") == desired["path"]
            and item.get("description") != desired["description"]
        ]
        same_destination_other = [
            item for item in observed_tasks
            if (item.get("attributes") or {}).get("bucket") == desired["attributes"]["bucket"]
            and normalize_folder((item.get("attributes") or {}).get("folder")) == desired["attributes"]["folder"]
            and item.get("description") != desired["description"]
        ]

        observed = same_description[0] if same_description else None
        action = "CREATE"
        reason = "task_absent"
        changed_fields = []

        if len(same_description) > 1:
            action = "BLOCK"
            reason = "task_description_not_unique"
        elif same_path_other:
            action = "BLOCK"
            reason = "local_path_owned_by_other_task"
        elif same_destination_other:
            action = "BLOCK"
            reason = "remote_destination_owned_by_other_task"
        elif observed is not None:
            current = managed_projection(observed)
            desired_identity_attrs = {
                "bucket": desired["attributes"]["bucket"],
                "folder": desired["attributes"]["folder"],
            }
            current_identity_attrs = {
                "bucket": current["attributes"]["bucket"],
                "folder": current["attributes"]["folder"],
            }

            identity_drift = [
                field for field in IDENTITY_FIELDS
                if current.get(field) != desired.get(field)
            ]
            if current_identity_attrs != desired_identity_attrs:
                identity_drift.append("attributes.destination")

            if identity_drift:
                action = "BLOCK"
                reason = "sync_identity_or_scope_drift"
                changed_fields = sorted(set(identity_drift))
            else:
                if current["attributes"]["fast_list"] != desired["attributes"]["fast_list"]:
                    changed_fields.append("attributes.fast_list")
                for field in MUTABLE_FIELDS:
                    if current.get(field) != desired.get(field):
                        changed_fields.append(field)

                if changed_fields:
                    action = "UPDATE"
                    reason = "mutable_task_drift"
                else:
                    action = "NOOP"
                    reason = "task_matches"

        if action == "BLOCK":
            issues.append(f"{desired['description']}:{reason}")

        task_plan.append({
            "description": desired["description"],
            "action": action,
            "reason": reason,
            "changed_fields": changed_fields,
            "desired": desired,
            "observed": managed_projection(observed) if observed is not None else None,
        })

    result = "PASS" if all(gates.values()) and credential_action != "BLOCK" and not any(
        item["action"] == "BLOCK" for item in task_plan
    ) else "BLOCK"

    module.exit_json(
        changed=False,
        result=result,
        credential={
            "name": credential_name,
            "provider_type": provider_type,
            "action": credential_action,
            "reason": credential_reason,
            "observed": credential_observed,
        },
        tasks=task_plan,
        gates=gates,
        issues=issues,
    )


if __name__ == "__main__":
    main()
