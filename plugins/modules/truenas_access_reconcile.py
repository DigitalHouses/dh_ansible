#!/usr/bin/python
# -*- coding: utf-8 -*-

from __future__ import annotations

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
        self.next_id = 1
        self.ws = None

    def __enter__(self):
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        self.ws = connect(
            "wss://{}:{}{}".format(self.host, self.port, self.endpoint),
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
    safe = safe_rpc_error(error)
    return (
        safe.get("errname") == "EBUSY"
        or "rate limit" in str(safe.get("reason") or "").lower()
        or "rate limit" in str(safe.get("message") or "").lower()
    )


def login_api_key(client, username, api_key, attempts=6):
    delay = 1.0
    for attempt in range(attempts):
        try:
            result = client.call(
                "auth.login_ex",
                [{
                    "mechanism": "API_KEY_PLAIN",
                    "username": username,
                    "api_key": api_key,
                    "login_options": {"user_info": False},
                }],
            )
        except RpcError as exc:
            if is_auth_rate_limit(exc) and attempt + 1 < attempts:
                time.sleep(delay)
                delay = min(delay * 2.0, 8.0)
                continue
            raise
        return isinstance(result, dict) and result.get("response_type") == "SUCCESS"
    return False


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
                    "TrueNAS job {} ended in {}: {}".format(
                        job_id,
                        state,
                        job.get("error") or job.get("exception") or "unknown error",
                    )
                )
        time.sleep(0.5)
    raise TimeoutError("Timed out waiting for TrueNAS job {}.".format(job_id))


def call_job(client, method, params, timeout):
    result = client.call(method, params)
    if isinstance(result, int) and not isinstance(result, bool):
        return wait_job(client, result, timeout)
    return result


def full_path(pool_name, relative_path):
    relative_path = str(relative_path or "")
    if relative_path.startswith("/") or "\x00" in relative_path:
        raise ValueError("absolute_or_invalid_relative_path")
    parts = [part for part in relative_path.split("/") if part]
    if any(part in (".", "..") for part in parts):
        raise ValueError("relative_path_escape")
    root = "/mnt/" + pool_name
    return root if not parts else root + "/" + "/".join(parts)


def perm_dict(value):
    if not isinstance(value, str) or len(value) != 3:
        raise ValueError("invalid_posix_permission")
    expected = "rwx"
    for index, char in enumerate(value):
        if char not in ("-", expected[index]):
            raise ValueError("invalid_posix_permission")
    return {
        "READ": value[0] == "r",
        "WRITE": value[1] == "w",
        "EXECUTE": value[2] == "x",
    }


def perm_string(perms):
    perms = perms or {}
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
            perm_string(entry.get("perms")),
        ))
    return sorted(result, key=lambda item: (item[2], item[0] or "", item[1] or "", item[3]))


def normalized_desired_acl(entries):
    result = []
    for entry in entries or []:
        tag = entry.get("tag")
        who = entry.get("who") if tag in ("USER", "GROUP") else None
        result.append((
            tag,
            who,
            bool(entry.get("default", False)),
            entry.get("perms"),
        ))
    return sorted(result, key=lambda item: (item[2], item[0] or "", item[1] or "", item[3] or ""))


def safe_initial_acl(observed):
    baseline = sorted([
        ("USER_OBJ", None, False, "rwx"),
        ("GROUP_OBJ", None, False, "r-x"),
        ("OTHER", None, False, "r-x"),
    ])
    return (
        observed.get("acltype") == "POSIX1E"
        and observed.get("uid") == 0
        and observed.get("gid") == 0
        and bool(observed.get("trivial"))
        and normalized_observed_acl(observed.get("acl")) == baseline
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

def desired_acl_matches(observed, desired):
    return (
        observed.get("acltype") == "POSIX1E"
        and observed.get("user") == desired.get("owner")
        and observed.get("group") == desired.get("group")
        and normalized_observed_acl(observed.get("acl"))
        == normalized_desired_acl(desired.get("entries"))
    )


def acl_payload(path, desired):
    dacl = []
    for entry in desired.get("entries") or []:
        item = {
            "tag": entry["tag"],
            "perms": perm_dict(entry["perms"]),
            "default": bool(entry.get("default", False)),
        }
        if entry["tag"] in ("USER", "GROUP"):
            item["who"] = entry["who"]
        dacl.append(item)
    return {
        "path": path,
        "dacl": dacl,
        "user": desired["owner"],
        "group": desired["group"],
        "acltype": "POSIX1E",
        "options": {
            "stripacl": False,
            "recursive": False,
            "traverse": False,
            "canonicalize": True,
            "validate_effective_acl": True,
        },
    }


def smb_guest(share):
    if "guestok" in share:
        return bool(share.get("guestok"))
    options = share.get("options")
    if isinstance(options, dict):
        return bool(options.get("guestok", False))
    return False


def smb_matches(observed, desired, path):
    return (
        str(observed.get("name") or "").lower() == str(desired.get("name") or "").lower()
        and observed.get("path") == path
        and observed.get("purpose") == desired.get("purpose")
        and bool(observed.get("enabled")) == bool(desired.get("enabled"))
        and bool(observed.get("readonly")) == bool(desired.get("readonly"))
        and bool(observed.get("browsable", True)) == bool(desired.get("browsable"))
        and smb_guest(observed) == bool(desired.get("guest"))
    )


def smb_payload(desired, path):
    if desired.get("guest"):
        raise ValueError("guest_smb_forbidden")
    return {
        "purpose": desired["purpose"],
        "name": desired["name"],
        "path": path,
        "enabled": bool(desired.get("enabled")),
        "comment": "Managed by Ansimble",
        "readonly": bool(desired.get("readonly")),
        "browsable": bool(desired.get("browsable")),
        "access_based_share_enumeration": False,
    }


def nfs_matches(observed, desired, path):
    return (
        observed.get("path") == path
        and sorted(observed.get("hosts") or []) == sorted(desired.get("hosts") or [])
        and sorted(observed.get("networks") or []) == sorted(desired.get("networks") or [])
        and bool(observed.get("ro")) == bool(desired.get("read_only"))
        and observed.get("maproot_user") in (None, "")
        and observed.get("maproot_group") in (None, "")
        and observed.get("mapall_user") == desired.get("mapall_user")
        and observed.get("mapall_group") == desired.get("mapall_group")
        and sorted(observed.get("security") or []) == sorted(desired.get("security") or [])
        and bool(observed.get("enabled")) == bool(desired.get("enabled"))
    )


def nfs_payload(desired, path):
    hosts = list(desired.get("hosts") or [])
    networks = list(desired.get("networks") or [])
    if not hosts and not networks:
        raise ValueError("open_nfs_export_forbidden")
    if any(value in ("*", "0.0.0.0/0", "::/0") for value in hosts + networks):
        raise ValueError("open_nfs_export_forbidden")
    return {
        "path": path,
        "aliases": [],
        "comment": "Managed by Ansimble",
        "networks": networks,
        "hosts": hosts,
        "ro": bool(desired.get("read_only")),
        "maproot_user": None,
        "maproot_group": None,
        "mapall_user": desired.get("mapall_user"),
        "mapall_group": desired.get("mapall_group"),
        "security": list(desired.get("security") or []),
        "enabled": bool(desired.get("enabled")),
    }


def action_map(items, key):
    return {item[key]: item for item in items or []}


def main():
    module = AnsibleModule(
        argument_spec=dict(
            host=dict(type="str", required=True),
            port=dict(type="int", default=443),
            endpoint=dict(type="str", default="/api/current"),
            tls_sha256_fingerprint=dict(type="str", required=True),
            username=dict(type="str", required=True),
            api_key=dict(type="str", required=True, no_log=True),
            pool_name=dict(type="str", required=True),
            desired_access=dict(type="dict", required=True),
            plan=dict(type="dict", required=True),
            timeout=dict(type="int", default=120),
        ),
        supports_check_mode=False,
    )

    if connect is None:
        module.fail_json(msg="Python dependency 'websockets' is required.")

    desired = module.params["desired_access"]
    plan = module.params["plan"]
    pool = module.params["pool_name"]

    if plan.get("result") != "PASS" or plan.get("issues"):
        module.fail_json(msg="TrueNAS access apply requires a clean PASS plan.")

    acl_cfg = desired.get("acl") or {}
    if (
        acl_cfg.get("model") != "POSIX1E"
        or bool(acl_cfg.get("recursive"))
        or bool(acl_cfg.get("traverse"))
    ):
        module.fail_json(msg="Only non-recursive, non-traversing POSIX1E ACL apply is supported.")

    if any(
        str(item.get("relative_path") or "") == ""
        for item in (acl_cfg.get("paths") or [])
    ):
        module.fail_json(
            msg="TrueNAS access layer must not mutate the ZFS pool root mountpoint."
        )

    for collection in (
        plan.get("acl_actions") or [],
        plan.get("smb_actions") or [],
        plan.get("nfs_actions") or [],
    ):
        if any(item.get("action") == "BLOCK" for item in collection):
            module.fail_json(msg="TrueNAS access plan contains a blocked operation.")

    expected = normalize_fingerprint(module.params["tls_sha256_fingerprint"])
    try:
        actual = tls_peer_fingerprint(
            module.params["host"],
            module.params["port"],
            module.params["timeout"],
        )
    except Exception as exc:
        module.fail_json(msg="Unable to read TrueNAS TLS certificate: {}: {}".format(type(exc).__name__, exc))

    if actual != expected:
        module.fail_json(
            msg="TrueNAS TLS fingerprint mismatch. Authentication was not attempted.",
            expected_tls_sha256=colonize(expected),
            actual_tls_sha256=colonize(actual),
        )

    changed = False
    operations = []

    acl_plan = action_map(plan.get("acl_actions"), "path")
    smb_plan = {str(item.get("name") or "").lower(): item for item in plan.get("smb_actions") or []}
    nfs_plan = action_map(plan.get("nfs_actions"), "path")
    service_plan = {item.get("service"): item for item in plan.get("service_actions") or []}

    try:
        with Client(
            module.params["host"],
            module.params["port"],
            module.params["endpoint"],
            module.params["timeout"],
        ) as client:
            if not login_api_key(client, module.params["username"], module.params["api_key"]):
                module.fail_json(msg="TrueNAS write API authentication failed.")

            # ACLs first. Re-read each path immediately before a write and only
            # accept either the exact desired state or the known fresh baseline.
            for acl in acl_cfg.get("paths") or []:
                path = full_path(pool, acl.get("relative_path"))
                planned = acl_plan.get(path)
                if not planned:
                    module.fail_json(msg="ACL desired path is absent from the accepted plan.", path=path)

                observed = client.call("filesystem.getacl", [path, False, True])
                if desired_acl_matches(observed, acl):
                    operations.append({"kind": "acl", "path": path, "action": "NOOP"})
                    continue

                if planned.get("action") != "UPDATE":
                    module.fail_json(
                        msg="ACL live state no longer matches the accepted plan.",
                        path=path,
                        planned_action=planned.get("action"),
                    )
                if not (safe_initial_acl(observed) or
                        safe_filebrowser_rw_upgrade(observed, acl, path, pool)):
                    module.fail_json(
                        msg="ACL changed after plan and is not the known fresh baseline.",
                        path=path,
                    )

                if acl.get("require_empty_before_first_apply"):
                    entries = client.call(
                        "filesystem.listdir",
                        [path, [], {"select": ["name", "type"]}],
                    ) or []
                    if entries:
                        module.fail_json(
                            msg="ACL first apply blocked because the protected path is no longer empty.",
                            path=path,
                            entry_count=len(entries),
                        )

                call_job(
                    client,
                    "filesystem.setacl",
                    [acl_payload(path, acl)],
                    module.params["timeout"],
                )
                verify_acl = client.call("filesystem.getacl", [path, False, True])
                if not desired_acl_matches(verify_acl, acl):
                    module.fail_json(msg="ACL verification failed after write.", path=path)
                changed = True
                operations.append({"kind": "acl", "path": path, "action": "UPDATE"})

            # SMB shares second. Any undeclared share under the owned pool is
            # blocked again before the first SMB write.
            root = "/mnt/" + pool
            observed_smb = query(client, "sharing.smb.query")
            desired_smb = desired.get("smb", {}).get("shares") or []
            desired_smb_names = {str(item["name"]).lower() for item in desired_smb}
            for share in observed_smb:
                path = str(share.get("path") or "")
                if (path == root or path.startswith(root + "/")) and str(share.get("name") or "").lower() not in desired_smb_names:
                    module.fail_json(msg="Unexpected SMB share appeared after plan.", share=share.get("name"), path=path)

            for share in desired_smb:
                path = full_path(pool, share.get("relative_path"))
                key = str(share["name"]).lower()
                planned = smb_plan.get(key)
                if not planned:
                    module.fail_json(msg="SMB desired share is absent from the accepted plan.", share=share["name"])

                observed_smb = query(client, "sharing.smb.query")
                same_name = [item for item in observed_smb if str(item.get("name") or "").lower() == key]
                if len(same_name) > 1:
                    module.fail_json(msg="SMB share name is not unique.", share=share["name"])
                observed = same_name[0] if same_name else None

                if observed and smb_matches(observed, share, path):
                    operations.append({"kind": "smb", "name": share["name"], "action": "NOOP"})
                    continue

                payload = smb_payload(share, path)
                if observed is None:
                    if planned.get("action") != "CREATE":
                        module.fail_json(msg="SMB live state no longer matches CREATE plan.", share=share["name"])
                    path_conflicts = [item for item in observed_smb if item.get("path") == path]
                    if path_conflicts:
                        module.fail_json(msg="SMB path became owned by another share after plan.", path=path)
                    client.call("sharing.smb.create", [payload])
                    action = "CREATE"
                else:
                    if planned.get("action") != "UPDATE":
                        module.fail_json(msg="SMB live state no longer matches UPDATE plan.", share=share["name"])
                    client.call("sharing.smb.update", [observed["id"], payload])
                    action = "UPDATE"

                verify = query(client, "sharing.smb.query", [["name", "=", share["name"]]], {})
                if len(verify) != 1 or not smb_matches(verify[0], share, path):
                    module.fail_json(msg="SMB verification failed after write.", share=share["name"])
                changed = True
                operations.append({"kind": "smb", "name": share["name"], "action": action})

            # NFS global protocol mode and exports.
            nfs_cfg = desired.get("nfs") or {}
            desired_protocols = list(nfs_cfg.get("protocols") or [])
            if desired_protocols != ["NFSV4"]:
                module.fail_json(msg="Fleet access apply requires NFSV4-only protocol declaration.")

            observed_nfs_cfg = client.call("nfs.config")
            planned_nfs_cfg = plan.get("nfs_config_action") or {}
            if list(observed_nfs_cfg.get("protocols") or []) != desired_protocols:
                if planned_nfs_cfg.get("action") != "UPDATE":
                    module.fail_json(msg="NFS protocol state changed after plan.")
                client.call("nfs.update", [{"protocols": desired_protocols}])
                verify_cfg = client.call("nfs.config")
                if list(verify_cfg.get("protocols") or []) != desired_protocols:
                    module.fail_json(msg="NFS protocol verification failed after write.")
                changed = True
                operations.append({"kind": "nfs_config", "action": "UPDATE"})
            else:
                operations.append({"kind": "nfs_config", "action": "NOOP"})

            observed_nfs = query(client, "sharing.nfs.query")
            desired_exports = nfs_cfg.get("exports") or []
            desired_nfs_paths = {full_path(pool, item.get("relative_path")) for item in desired_exports}
            for export in observed_nfs:
                path = str(export.get("path") or "")
                if (path == root or path.startswith(root + "/")) and path not in desired_nfs_paths:
                    module.fail_json(msg="Unexpected NFS export appeared after plan.", path=path)

            for export in desired_exports:
                path = full_path(pool, export.get("relative_path"))
                planned = nfs_plan.get(path)
                if not planned:
                    module.fail_json(msg="NFS desired export is absent from the accepted plan.", path=path)

                observed_nfs = query(client, "sharing.nfs.query")
                same_path = [item for item in observed_nfs if item.get("path") == path]
                if len(same_path) > 1:
                    module.fail_json(msg="NFS export path is not unique.", path=path)
                observed = same_path[0] if same_path else None

                if observed and nfs_matches(observed, export, path):
                    operations.append({"kind": "nfs", "path": path, "action": "NOOP"})
                    continue

                payload = nfs_payload(export, path)
                if observed is None:
                    if planned.get("action") != "CREATE":
                        module.fail_json(msg="NFS live state no longer matches CREATE plan.", path=path)
                    client.call("sharing.nfs.create", [payload])
                    action = "CREATE"
                else:
                    if planned.get("action") != "UPDATE":
                        module.fail_json(msg="NFS live state no longer matches UPDATE plan.", path=path)
                    client.call("sharing.nfs.update", [observed["id"], payload])
                    action = "UPDATE"

                verify = query(client, "sharing.nfs.query", [["path", "=", path]], {})
                if len(verify) != 1 or not nfs_matches(verify[0], export, path):
                    module.fail_json(msg="NFS verification failed after write.", path=path)
                changed = True
                operations.append({"kind": "nfs", "path": path, "action": action})

            # Services last. Enable-on-boot and runtime state are independent.
            observed_services = {item.get("service"): item for item in query(client, "service.query")}
            for _, service_desired in (desired.get("services") or {}).items():
                service = service_desired.get("service")
                planned = service_plan.get(service)
                observed = observed_services.get(service)
                if not planned or not observed:
                    module.fail_json(msg="Service state is absent from the accepted plan.", service=service)

                desired_enable = bool(service_desired.get("enable"))
                if bool(observed.get("enable")) != desired_enable:
                    if planned.get("enable_action") != "UPDATE":
                        module.fail_json(msg="Service enable state changed after plan.", service=service)
                    client.call("service.update", [service, {"enable": desired_enable}])
                    changed = True
                    operations.append({"kind": "service_enable", "service": service, "action": "UPDATE"})
                else:
                    operations.append({"kind": "service_enable", "service": service, "action": "NOOP"})

                observed = {item.get("service"): item for item in query(client, "service.query")}.get(service)
                running = observed and observed.get("state") == "RUNNING"
                desired_running = bool(service_desired.get("running"))
                runtime_action = planned.get("runtime_action")

                if running != desired_running:
                    expected_action = "START" if desired_running else "STOP"
                    if runtime_action != expected_action:
                        module.fail_json(msg="Service runtime state changed after plan.", service=service)
                    result = call_job(
                        client,
                        "service.control",
                        [expected_action, service, {"silent": False}],
                        module.params["timeout"],
                    )
                    if result is False:
                        module.fail_json(msg="Service control returned false.", service=service, action=expected_action)
                    changed = True
                    operations.append({"kind": "service_runtime", "service": service, "action": expected_action})
                else:
                    operations.append({"kind": "service_runtime", "service": service, "action": "NOOP"})

                verify_service = {item.get("service"): item for item in query(client, "service.query")}.get(service)
                if not verify_service:
                    module.fail_json(msg="Service disappeared during verification.", service=service)
                if bool(verify_service.get("enable")) != desired_enable:
                    module.fail_json(msg="Service enable verification failed.", service=service)
                if (verify_service.get("state") == "RUNNING") != desired_running:
                    module.fail_json(msg="Service runtime verification failed.", service=service)

    except RpcError as exc:
        module.fail_json(
            msg="TrueNAS access JSON-RPC method failed.",
            rpc_error=safe_rpc_error(exc),
            operations=operations,
        )
    except Exception as exc:
        module.fail_json(
            msg="TrueNAS access reconciliation failed: {}: {}".format(type(exc).__name__, exc),
            operations=operations,
        )

    module.exit_json(
        changed=changed,
        result="PASS",
        operations=operations,
        tls_sha256_fingerprint=colonize(actual),
    )


if __name__ == "__main__":
    main()
