#!/usr/bin/python
# -*- coding: utf-8 -*-

from __future__ import annotations

from ansible.module_utils.basic import AnsibleModule


BASELINE_ACL = [
    ("USER_OBJ", None, False, "rwx"),
    ("GROUP_OBJ", None, False, "r-x"),
    ("OTHER", None, False, "r-x"),
]


def perm_string(perms):
    return "".join([
        "r" if perms.get("READ") else "-",
        "w" if perms.get("WRITE") else "-",
        "x" if perms.get("EXECUTE") else "-",
    ])


def normalized_observed_acl(entries):
    result = []
    for entry in entries or []:
        tag = entry.get("tag")
        who = entry.get("who") if tag in ("USER", "GROUP") else None
        result.append((
            tag,
            who,
            bool(entry.get("default", False)),
            perm_string(entry.get("perms") or {}),
        ))
    return sorted(result, key=lambda item: (item[2], item[0] or "", item[1] or "", item[3]))


def normalized_desired_acl(entries):
    result = []
    for entry in entries or []:
        tag = entry.get("tag")
        who = entry.get("who") if tag in ("USER", "GROUP") else None
        result.append((tag, who, bool(entry.get("default", False)), entry.get("perms")))
    return sorted(result, key=lambda item: (item[2], item[0] or "", item[1] or "", item[3] or ""))


def full_path(pool_name, relative_path):
    relative_path = str(relative_path or "")
    if relative_path.startswith("/") or "\x00" in relative_path:
        raise ValueError("absolute_or_invalid_relative_path")
    parts = [part for part in relative_path.split("/") if part]
    if any(part in (".", "..") for part in parts):
        raise ValueError("relative_path_escape")
    root = "/mnt/" + pool_name
    return root if not parts else root + "/" + "/".join(parts)


def validate_posix_entries(entries):
    issues = []
    valid_tags = {"USER_OBJ", "GROUP_OBJ", "OTHER", "MASK", "USER", "GROUP"}
    seen = set()
    by_default = {False: [], True: []}

    for entry in entries or []:
        tag = entry.get("tag")
        perms = entry.get("perms")
        default = bool(entry.get("default", False))
        who = entry.get("who") if tag in ("USER", "GROUP") else None
        key = (tag, who, default)
        if tag not in valid_tags:
            issues.append("invalid_acl_tag")
        if not isinstance(perms, str) or len(perms) != 3 or any(
            perms[i] not in ("-", expected) for i, expected in enumerate("rwx")
        ):
            issues.append("invalid_acl_perms")
        if tag in ("USER", "GROUP") and not who:
            issues.append("named_acl_entry_without_who")
        if tag not in ("USER", "GROUP") and entry.get("who") is not None:
            issues.append("object_acl_entry_with_who")
        if key in seen:
            issues.append("duplicate_acl_entry")
        seen.add(key)
        by_default[default].append(tag)

    for default, tags in by_default.items():
        if not tags:
            continue
        for required in ("USER_OBJ", "GROUP_OBJ", "OTHER"):
            if tags.count(required) != 1:
                issues.append(("default_" if default else "access_") + "acl_missing_" + required.lower())
        if any(tag in ("USER", "GROUP") for tag in tags) and tags.count("MASK") != 1:
            issues.append(("default_" if default else "access_") + "acl_missing_mask")
    return issues


def safe_initial_acl(observed):
    return (
        observed.get("acltype") == "POSIX1E"
        and observed.get("uid") == 0
        and observed.get("gid") == 0
        and bool(observed.get("trivial"))
        and normalized_observed_acl(observed.get("acl")) == sorted(BASELINE_ACL)
    )


def safe_filebrowser_rw_upgrade(observed, desired, path, pool):
    """Only the declared USER may gain write on the data/backups roots.

    This is intentionally a narrow, non-recursive exception for a previously
    accepted read-only ACL. Other users, ACL entries or directories block.
    """
    if not isinstance(path, str) or not isinstance(pool, str) or not pool:
        return False
    relative = path.removeprefix("/mnt/" + pool + "/")
    group = desired.get("group")
    entries = desired.get("entries")
    if not isinstance(entries, list):
        return False
    named_users = [
        entry for entry in entries if isinstance(entry, dict)
        and entry.get("tag") == "USER" and not entry.get("default")
    ]
    if len(named_users) != 1:
        return False
    user = named_users[0].get("who")
    if (not isinstance(user, str) or not user
            or not all(char.isalnum() or char in "_-." for char in user)):
        return False
    if (relative not in ("data", "backups")
            or path != "/mnt/" + pool + "/" + relative
            or desired.get("owner") != "root"
            or not isinstance(group, str) or not group
            or observed.get("acltype") != "POSIX1E"
            or observed.get("user") != "root"
            or observed.get("group") != group
            or observed.get("uid") != 0
            or bool(observed.get("trivial"))):
        return False
    base = [
        {"tag": "USER_OBJ", "perms": "rwx", "default": False},
        {"tag": "GROUP_OBJ", "perms": "--x", "default": False},
        {"tag": "USER", "who": user, "perms": "rwx", "default": False},
        {"tag": "MASK", "perms": "rwx", "default": False},
        {"tag": "OTHER", "perms": "---", "default": False},
    ]
    old = [dict(entry) for entry in base]
    for entry in old:
        if entry["tag"] in ("USER", "MASK"):
            entry["perms"] = "r-x"
    return (
        normalized_desired_acl(entries) == normalized_desired_acl(base)
        and normalized_observed_acl(observed.get("acl"))
        == normalized_desired_acl(old)
    )

def smb_guest(share):
    if "guestok" in share:
        return bool(share.get("guestok"))
    options = share.get("options")
    if isinstance(options, dict) and "guestok" in options:
        return bool(options.get("guestok"))
    return False


def main():
    module = AnsibleModule(
        argument_spec=dict(
            pool_name=dict(type="str", required=True),
            desired_access=dict(type="dict", required=True),
            desired_users=dict(type="list", elements="dict", required=True),
            desired_groups=dict(type="list", elements="dict", required=True),
            observed_acls=dict(type="list", elements="dict", required=True),
            directory_counts=dict(type="dict", required=True),
            observed_smb_shares=dict(type="list", elements="dict", required=True),
            observed_nfs_shares=dict(type="list", elements="dict", required=True),
            observed_services=dict(type="list", elements="dict", required=True),
            observed_nfs_config=dict(type="dict", required=True),
            observed_smb_config=dict(type="dict", required=True),
        ),
        supports_check_mode=True,
    )

    pool = module.params["pool_name"]
    desired = module.params["desired_access"]
    issues = []
    gates = {}
    acl_actions = []
    smb_actions = []
    nfs_actions = []
    service_actions = []

    users = {"root"} | {item.get("username") for item in module.params["desired_users"]}
    groups = {"wheel"} | {item.get("name") for item in module.params["desired_groups"]}

    acl_cfg = desired.get("acl") or {}
    gates["acl_model_posix1e"] = acl_cfg.get("model") == "POSIX1E"
    gates["acl_nonrecursive"] = not bool(acl_cfg.get("recursive")) and not bool(acl_cfg.get("traverse"))
    if not gates["acl_model_posix1e"]:
        issues.append("acl_model_must_be_POSIX1E")
    if not gates["acl_nonrecursive"]:
        issues.append("recursive_or_traverse_acl_change_forbidden")

    observed_acl_by_path = {item.get("path"): item for item in module.params["observed_acls"]}
    desired_paths = set()
    for item in acl_cfg.get("paths") or []:
        relative_path = str(item.get("relative_path") or "")
        if relative_path == "":
            root_path = "/mnt/" + pool
            issues.append("pool_root_acl_mutation_forbidden:" + root_path)
            acl_actions.append({
                "path": root_path,
                "action": "BLOCK",
                "reason": "pool_root_mountpoint_unmanaged",
            })
            continue
        try:
            path = full_path(pool, relative_path)
        except ValueError as exc:
            issues.append("unsafe_acl_path:" + str(exc))
            continue
        if path in desired_paths:
            issues.append("duplicate_acl_path:" + path)
            continue
        desired_paths.add(path)

        if item.get("owner") not in users:
            issues.append("unknown_acl_owner:" + str(item.get("owner")))
        if item.get("group") not in groups:
            issues.append("unknown_acl_group:" + str(item.get("group")))
        for entry in item.get("entries") or []:
            if entry.get("tag") == "USER" and entry.get("who") not in users:
                issues.append("unknown_acl_user:" + str(entry.get("who")))
            if entry.get("tag") == "GROUP" and entry.get("who") not in groups:
                issues.append("unknown_acl_group_entry:" + str(entry.get("who")))
        issues.extend(path + ":" + problem for problem in validate_posix_entries(item.get("entries")))

        observed = observed_acl_by_path.get(path)
        if not observed:
            issues.append("unknown_acl_state:" + path)
            acl_actions.append({"path": path, "action": "BLOCK", "reason": "not_observed"})
            continue

        target_acl = normalized_desired_acl(item.get("entries"))
        observed_acl = normalized_observed_acl(observed.get("acl"))
        matches = (
            observed.get("acltype") == "POSIX1E"
            and observed.get("user") == item.get("owner")
            and observed.get("group") == item.get("group")
            and observed_acl == target_acl
        )
        if matches:
            acl_actions.append({"path": path, "action": "NOOP", "reason": "acl_matches"})
            continue

        if not (safe_initial_acl(observed) or
                safe_filebrowser_rw_upgrade(observed, item, path, pool)):
            issues.append("unexpected_acl_state:" + path)
            acl_actions.append({"path": path, "action": "BLOCK", "reason": "unexpected_acl_state"})
            continue

        count = module.params["directory_counts"].get(path)
        if item.get("require_empty_before_first_apply") and count != 0:
            issues.append("nonempty_path_requires_explicit_acl_migration:" + path)
            acl_actions.append({
                "path": path,
                "action": "BLOCK",
                "reason": "nonempty_first_acl",
                "entry_count": count,
            })
            continue

        acl_actions.append({"path": path, "action": "UPDATE", "reason": ("known_fresh_baseline" if safe_initial_acl(observed) else "approved_filebrowser_rw_upgrade")})

    gates["desired_acl_paths_unique_and_safe"] = not any(
        issue.startswith((
            "unsafe_acl_path:",
            "duplicate_acl_path:",
            "unknown_acl_",
            "pool_root_acl_mutation_forbidden:",
        ))
        for issue in issues
    )
    gates["acl_transition_known"] = not any(
        issue.startswith(("unexpected_acl_state:", "nonempty_path_requires_explicit_acl_migration:"))
        for issue in issues
    )

    root = "/mnt/" + pool
    smb_cfg = desired.get("smb") or {}
    desired_smb = {}
    desired_smb_paths = {}
    for item in smb_cfg.get("shares") or []:
        name_key = str(item.get("name") or "").lower()
        try:
            path = full_path(pool, item.get("relative_path"))
        except ValueError as exc:
            issues.append("unsafe_smb_path:" + str(exc))
            continue
        if not name_key or name_key in desired_smb:
            issues.append("duplicate_smb_name:" + name_key)
            continue
        if path in desired_smb_paths:
            issues.append("duplicate_smb_path:" + path)
            continue
        if bool(item.get("guest")):
            issues.append("smb_guest_forbidden:" + name_key)
        desired_smb[name_key] = (item, path)
        desired_smb_paths[path] = name_key

    observed_smb_by_name = {}
    for share in module.params["observed_smb_shares"]:
        name_key = str(share.get("name") or "").lower()
        if name_key in observed_smb_by_name:
            issues.append("duplicate_observed_smb_name:" + name_key)
        observed_smb_by_name[name_key] = share
        path = str(share.get("path") or "")
        if path == root or path.startswith(root + "/"):
            if name_key not in desired_smb:
                issues.append("unexpected_smb_share:" + str(share.get("name")))

    for name_key, (item, path) in desired_smb.items():
        observed = observed_smb_by_name.get(name_key)
        if not observed:
            smb_actions.append({"name": item.get("name"), "path": path, "action": "CREATE"})
            continue
        if observed.get("path") != path:
            issues.append("smb_name_path_conflict:" + name_key)
            smb_actions.append({"name": item.get("name"), "path": path, "action": "BLOCK"})
            continue
        matches = (
            observed.get("purpose") == item.get("purpose")
            and bool(observed.get("enabled")) == bool(item.get("enabled"))
            and bool(observed.get("readonly")) == bool(item.get("readonly"))
            and bool(observed.get("browsable", True)) == bool(item.get("browsable"))
            and smb_guest(observed) == bool(item.get("guest"))
        )
        smb_actions.append({
            "name": item.get("name"),
            "path": path,
            "action": "NOOP" if matches else "UPDATE",
        })

    gates["smb_no_unexpected_owned_pool_shares"] = not any(
        issue.startswith(("unexpected_smb_share:", "smb_name_path_conflict:", "duplicate_observed_smb_name:"))
        for issue in issues
    )
    gates["smb_guest_disabled"] = not any(issue.startswith("smb_guest_forbidden:") for issue in issues)
    if smb_cfg.get("require_smb1_disabled"):
        gates["smb1_disabled"] = not bool(module.params["observed_smb_config"].get("enable_smb1"))
        if not gates["smb1_disabled"]:
            issues.append("smb1_enabled")
    if smb_cfg.get("require_ntlmv1_disabled"):
        gates["ntlmv1_disabled"] = not bool(module.params["observed_smb_config"].get("ntlmv1_auth"))
        if not gates["ntlmv1_disabled"]:
            issues.append("ntlmv1_enabled")

    nfs_cfg = desired.get("nfs") or {}
    protocols = nfs_cfg.get("protocols") or []
    gates["nfs4_only_declared"] = protocols == ["NFSV4"]
    if not gates["nfs4_only_declared"]:
        issues.append("nfs_protocols_must_be_NFSV4_only")

    desired_nfs = {}
    for item in nfs_cfg.get("exports") or []:
        try:
            path = full_path(pool, item.get("relative_path"))
        except ValueError as exc:
            issues.append("unsafe_nfs_path:" + str(exc))
            continue
        if path in desired_nfs:
            issues.append("duplicate_nfs_path:" + path)
            continue
        hosts = item.get("hosts") or []
        networks = item.get("networks") or []
        if not hosts and not networks:
            issues.append("open_nfs_export_forbidden:" + path)
        if any(value in ("0.0.0.0/0", "::/0", "*") for value in hosts + networks):
            issues.append("open_nfs_export_forbidden:" + path)
        if item.get("mapall_user") not in users:
            issues.append("unknown_nfs_mapall_user:" + str(item.get("mapall_user")))
        if item.get("mapall_group") not in groups:
            issues.append("unknown_nfs_mapall_group:" + str(item.get("mapall_group")))
        desired_nfs[path] = item

        overlapping = [
            smb_item for smb_item, smb_path in desired_smb.values()
            if path == smb_path or path.startswith(smb_path + "/")
        ]
        if overlapping and any(item2.get("purpose") != "MULTIPROTOCOL_SHARE" for item2 in overlapping):
            issues.append("multiprotocol_semantics_conflict:" + path)

    observed_nfs_by_path = {}
    for share in module.params["observed_nfs_shares"]:
        path = str(share.get("path") or "")
        if path in observed_nfs_by_path:
            issues.append("duplicate_observed_nfs_path:" + path)
        observed_nfs_by_path[path] = share
        if path == root or path.startswith(root + "/"):
            if path not in desired_nfs:
                issues.append("unexpected_nfs_share:" + path)

    for path, item in desired_nfs.items():
        observed = observed_nfs_by_path.get(path)
        if not observed:
            nfs_actions.append({"path": path, "action": "CREATE"})
            continue
        matches = (
            sorted(observed.get("hosts") or []) == sorted(item.get("hosts") or [])
            and sorted(observed.get("networks") or []) == sorted(item.get("networks") or [])
            and bool(observed.get("ro")) == bool(item.get("read_only"))
            and observed.get("mapall_user") == item.get("mapall_user")
            and observed.get("mapall_group") == item.get("mapall_group")
            and observed.get("maproot_user") in (None, "")
            and observed.get("maproot_group") in (None, "")
            and sorted(observed.get("security") or []) == sorted(item.get("security") or [])
            and bool(observed.get("enabled")) == bool(item.get("enabled"))
        )
        nfs_actions.append({"path": path, "action": "NOOP" if matches else "UPDATE"})

    gates["nfs_allowlists_strict"] = not any(issue.startswith("open_nfs_export_forbidden:") for issue in issues)
    gates["nfs_no_unexpected_owned_pool_shares"] = not any(
        issue.startswith(("unexpected_nfs_share:", "duplicate_observed_nfs_path:")) for issue in issues
    )
    gates["multiprotocol_semantics_safe"] = not any(
        issue.startswith("multiprotocol_semantics_conflict:") for issue in issues
    )

    observed_protocols = module.params["observed_nfs_config"].get("protocols") or []
    nfs_config_action = {
        "action": "NOOP" if observed_protocols == protocols else "UPDATE",
        "observed_protocols": observed_protocols,
        "desired_protocols": protocols,
    }

    observed_services = {item.get("service"): item for item in module.params["observed_services"]}
    for key, item in (desired.get("services") or {}).items():
        service = item.get("service")
        observed = observed_services.get(service)
        if not observed:
            issues.append("service_state_unknown:" + str(service))
            service_actions.append({"key": key, "service": service, "action": "BLOCK"})
            continue
        enable_action = "NOOP" if bool(observed.get("enable")) == bool(item.get("enable")) else "UPDATE"
        desired_running = bool(item.get("running"))
        running = observed.get("state") == "RUNNING"
        runtime_action = "NOOP" if running == desired_running else ("START" if desired_running else "STOP")
        service_actions.append({
            "key": key,
            "service": service,
            "enable_action": enable_action,
            "runtime_action": runtime_action,
        })

    gates["service_states_known"] = not any(issue.startswith("service_state_unknown:") for issue in issues)

    result = "PASS" if not issues and all(gates.values()) else "BLOCK"
    module.exit_json(
        changed=False,
        result=result,
        gates=gates,
        issues=issues,
        acl_actions=acl_actions,
        smb_actions=smb_actions,
        nfs_actions=nfs_actions,
        nfs_config_action=nfs_config_action,
        service_actions=service_actions,
    )


if __name__ == "__main__":
    main()
