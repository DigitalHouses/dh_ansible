#!/usr/bin/python
# -*- coding: utf-8 -*-

from __future__ import annotations

DOCUMENTATION = r"""
---
module: truenas_cloudsync_reconcile
short_description: Reconcile declared TrueNAS B2 Cloud Sync credentials and tasks
description:
  - Applies only an accepted read-only Cloud Sync plan.
  - Verifies pinned TLS and Backblaze B2 credentials before creating resources.
  - Never deletes Cloud Sync credentials or tasks.
  - Blocks unknown pre-existing remote SYNC destinations before creating a task.
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
  b2_account_id:
    type: str
    required: true
    no_log: true
  b2_application_key:
    type: str
    required: true
    no_log: true
  plan:
    type: dict
    required: true
  timeout:
    type: int
    default: 30
author:
  - DigitalHouses
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
    safe = safe_rpc_error(error)
    return (
        safe.get("errname") == "EBUSY"
        or "rate limit" in str(safe.get("reason") or "").lower()
        or "rate limit" in str(safe.get("message") or "").lower()
    )


def login_with_retry(client, username, api_key, attempts=6):
    delay = 1.0
    for attempt in range(attempts):
        try:
            result = client.call("auth.login_ex", [{
                "mechanism": "API_KEY_PLAIN",
                "username": username,
                "api_key": api_key,
                "login_options": {"user_info": False},
            }])
        except RpcError as exc:
            if is_auth_rate_limit(exc) and attempt + 1 < attempts:
                time.sleep(delay)
                delay = min(delay * 2.0, 8.0)
                continue
            raise
        return result
    return None


def query(client, method, filters=None, options=None):
    result = client.call(method, [filters or [], options or {}])
    return result if isinstance(result, list) else []


def wait_job_details(client, job_id, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        jobs = query(client, "core.get_jobs", [["id", "=", int(job_id)]], {})
        if jobs:
            job = jobs[0]
            state = str(job.get("state") or "").upper()
            if state == "SUCCESS":
                return job
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


def verify_adoption_dry_run(client, task_id, timeout):
    job_id = client.call("cloudsync.sync", [int(task_id), {"dry_run": True}])
    if not isinstance(job_id, int) or isinstance(job_id, bool):
        raise RuntimeError("Cloud Sync dry-run did not return a job id.")
    job = wait_job_details(client, job_id, timeout)
    logs = str(job.get("logs_excerpt") or "")
    no_changes = "There was nothing to transfer" in logs
    return no_changes, int(job_id)


def filesystem_file_summary(client, root, max_entries=10000):
    queue = [root]
    files = 0
    bytes_total = 0
    visited = 0
    while queue:
        path = queue.pop(0)
        entries = client.call(
            "filesystem.listdir",
            [path, [], {"select": ["name", "path", "type", "size"]}],
        )
        if not isinstance(entries, list):
            raise RuntimeError("filesystem.listdir did not return a list.")
        for entry in entries:
            if not isinstance(entry, dict):
                raise RuntimeError("filesystem.listdir returned an invalid entry.")
            name = str(entry.get("name") or "")
            if name == ".zfs":
                continue
            visited += 1
            if visited > max_entries:
                raise RuntimeError("Local Cloud Sync source traversal exceeded safety limit.")
            entry_type = str(entry.get("type") or "").upper()
            entry_path = entry.get("path")
            if entry_type == "DIRECTORY":
                if not isinstance(entry_path, str) or not entry_path.startswith(root.rstrip("/") + "/"):
                    raise RuntimeError("Local Cloud Sync source traversal escaped declared root.")
                queue.append(entry_path)
            else:
                files += 1
                size = entry.get("size")
                if isinstance(size, int) and size > 0:
                    bytes_total += size
    return {"files": files, "bytes": bytes_total}


def run_sync_onetime(client, desired, credential_id, direction, transfer_mode, dry_run, timeout):
    payload = task_payload(desired, credential_id)
    payload["direction"] = direction
    payload["transfer_mode"] = transfer_mode
    payload["enabled"] = False
    if direction == "PULL":
        # TrueNAS validates a PULL folder by listing its parent and matching the
        # basename. Bucket-provider paths are accepted in relative form.
        attributes = dict(payload.get("attributes") or {})
        attributes["folder"] = str(attributes.get("folder") or "").lstrip("/")
        payload["attributes"] = attributes
    job_id = client.call(
        "cloudsync.sync_onetime",
        [payload, {"dry_run": bool(dry_run)}],
    )
    if not isinstance(job_id, int) or isinstance(job_id, bool):
        raise RuntimeError("Cloud Sync one-time operation did not return a job id.")
    job = wait_job_details(client, job_id, timeout)
    return job, int(job_id)


def remote_entry_fields(item):
    name = item.get("Name") if "Name" in item else item.get("name")
    is_dir = item.get("IsDir") if "IsDir" in item else item.get("is_dir")
    size = item.get("Size") if "Size" in item else item.get("size")
    return str(name or ""), bool(is_dir), size


def safe_remote_child_name(value):
    return (
        isinstance(value, str)
        and value not in ("", ".", "..")
        and "/" not in value
        and "\\" not in value
    )


def restore_remote_children(
    client,
    desired,
    credential_id,
    target_listing,
    timeout,
):
    children = []
    for item in target_listing if isinstance(target_listing, list) else []:
        if not isinstance(item, dict):
            raise RuntimeError("Remote Cloud Sync directory listing returned an invalid entry.")
        name, is_dir, size = remote_entry_fields(item)
        if name.strip("/") == ".bzEmpty" and size == 0 and not is_dir:
            continue
        if not safe_remote_child_name(name):
            raise RuntimeError("Remote Cloud Sync recovery child name is unsafe.")
        if not is_dir:
            raise RuntimeError(
                "Remote Cloud Sync recovery prefix contains a root-level file; "
                "automatic child-directory restore is blocked."
            )
        children.append(name)

    if not children:
        raise RuntimeError(
            "Remote Cloud Sync recovery prefix contains user objects but no "
            "restorable child directories."
        )

    job_ids = []
    for child in children:
        child_desired = dict(desired)
        child_desired["path"] = desired["path"].rstrip("/") + "/" + child
        attributes = dict(desired.get("attributes") or {})
        attributes["folder"] = (
            str(attributes.get("folder") or "").rstrip("/") + "/" + child
        )
        child_desired["attributes"] = attributes
        _, job_id = run_sync_onetime(
            client,
            child_desired,
            credential_id,
            "PULL",
            "COPY",
            False,
            timeout,
        )
        job_ids.append(job_id)

    return children, job_ids


def safe_relative_path(value):
    if not isinstance(value, str) or not value or value.startswith("/"):
        return False
    return all(part not in ("", ".", "..") for part in value.split("/"))


def normalize_folder(value):
    if value in ("", "/"):
        return "/"
    return "/" + str(value).strip("/")


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
    return {
        "description": item["description"],
        "path": f"/mnt/{pool_name}/{item['relative_path']}",
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


def task_payload(item, credential_id):
    return {
        "description": item["description"],
        "path": item["path"],
        "credentials": credential_id,
        "attributes": item["attributes"],
        "schedule": item["schedule"],
        "pre_script": "",
        "post_script": "",
        "snapshot": item["snapshot"],
        "include": item["include"],
        "exclude": item["exclude"],
        "enabled": item["enabled"],
        "bwlimit": item["bwlimit"],
        "transfers": item["transfers"],
        "direction": item["direction"],
        "transfer_mode": item["transfer_mode"],
        "encryption": item["encryption"],
        "filename_encryption": item["filename_encryption"],
        "encryption_password": "",
        "encryption_salt": "",
        "create_empty_src_dirs": item["create_empty_src_dirs"],
        "follow_symlinks": item["follow_symlinks"],
    }


def task_projection(item):
    credentials = item.get("credentials") if isinstance(item.get("credentials"), dict) else {}
    provider = credentials.get("provider") if isinstance(credentials.get("provider"), dict) else {}
    attributes = item.get("attributes") if isinstance(item.get("attributes"), dict) else {}
    return {
        "id": item.get("id"),
        "description": item.get("description"),
        "path": item.get("path"),
        "credential_name": credentials.get("name"),
        "credential_provider_type": provider.get("type"),
        "attributes": {
            "bucket": attributes.get("bucket"),
            "folder": normalize_folder(attributes.get("folder")),
            "fast_list": bool(attributes.get("fast_list", False)),
        },
        "schedule": normalized_schedule(item.get("schedule")),
        "pre_script_present": bool(str(item.get("pre_script") or "").strip()),
        "post_script_present": bool(str(item.get("post_script") or "").strip()),
        "args_present": bool(str(item.get("args") or "").strip()),
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


def task_matches(observed, desired):
    current = task_projection(observed)
    for key in (
        "description", "path", "credential_name", "credential_provider_type",
        "schedule", "snapshot", "include", "exclude", "enabled", "bwlimit",
        "transfers", "direction", "transfer_mode", "encryption",
        "filename_encryption", "create_empty_src_dirs", "follow_symlinks",
        "pre_script_present", "post_script_present", "args_present", "locked",
    ):
        if current.get(key) != desired.get(key):
            return False
    return current.get("attributes") == desired.get("attributes")


def remote_listing_has_user_objects(entries):
    if not isinstance(entries, list):
        return True
    for item in entries:
        if not isinstance(item, dict):
            return True
        name = item.get("Name") or item.get("name") or item.get("Path") or item.get("path")
        size = item.get("Size") if "Size" in item else item.get("size")
        is_dir = item.get("IsDir") if "IsDir" in item else item.get("is_dir")
        if str(name or "").strip("/") == ".bzEmpty" and size == 0 and not bool(is_dir):
            continue
        return True
    return False


def main():
    module = AnsibleModule(
        argument_spec=dict(
            host=dict(type="str", required=True),
            port=dict(type="int", default=443),
            endpoint=dict(type="str", default="/api/current"),
            tls_sha256_fingerprint=dict(type="str", required=True),
            username=dict(type="str", required=True),
            api_key=dict(type="str", required=True, no_log=True),
            desired_credential=dict(type="dict", required=True),
            desired_tasks=dict(type="list", elements="dict", required=True),
            pool_name=dict(type="str", required=True),
            b2_account_id=dict(type="str", required=True, no_log=True),
            b2_application_key=dict(type="str", required=True, no_log=True),
            plan=dict(type="dict", required=True),
            timeout=dict(type="int", default=30),
            adoption_timeout=dict(type="int", default=900),
            restore_existing_remote_on_empty_local=dict(type="bool", default=False, no_log=False),
            restore_timeout=dict(type="int", default=1800),
        ),
        supports_check_mode=False,
    )

    if connect is None:
        module.fail_json(msg="Python dependency 'websockets' is required on the Ansible runner.")

    plan = module.params["plan"]
    if plan.get("result") != "PASS":
        module.fail_json(msg="Cloud Sync apply requires an accepted PASS plan.")

    credential = module.params["desired_credential"]
    desired_items = module.params["desired_tasks"]
    pool_name = module.params["pool_name"]

    if credential.get("provider_type") != "B2":
        module.fail_json(msg="This Cloud Sync reconciler currently supports only B2 credentials.")
    if not module.params["b2_account_id"] or not module.params["b2_application_key"]:
        module.fail_json(msg="Backblaze B2 recovery credentials are incomplete.")
    if "/" in pool_name or pool_name in ("", ".", ".."):
        module.fail_json(msg="Unsafe pool name in Cloud Sync declaration.")
    for item in desired_items:
        if not safe_relative_path(item.get("relative_path")):
            module.fail_json(msg="Unsafe relative path in Cloud Sync declaration.")
        if item.get("direction") != "PUSH" or item.get("transfer_mode") != "SYNC":
            module.fail_json(msg="Cloud backup contract permits PUSH+SYNC tasks only.")
        if not isinstance(item.get("adopt_existing_remote_prefix", False), bool):
            module.fail_json(msg="Cloud backup remote-prefix adoption policy must be boolean.")

    plan_by_description = {
        item.get("description"): item
        for item in plan.get("tasks") or []
    }
    if len(plan_by_description) != len(desired_items):
        module.fail_json(msg="Accepted Cloud Sync plan does not cover every desired task.")

    allowed_task_actions = {"CREATE", "UPDATE", "NOOP"}
    if any(item.get("action") not in allowed_task_actions for item in plan.get("tasks") or []):
        module.fail_json(msg="Accepted Cloud Sync plan contains a non-applicable task action.")
    credential_action = (plan.get("credential") or {}).get("action")
    if credential_action not in {"CREATE", "NOOP"}:
        module.fail_json(msg="Accepted Cloud Sync plan contains a non-applicable credential action.")

    expected = normalize_fingerprint(module.params["tls_sha256_fingerprint"])
    try:
        actual = tls_peer_fingerprint(module.params["host"], module.params["port"], module.params["timeout"])
    except Exception as exc:
        module.fail_json(msg=f"Unable to read TrueNAS TLS certificate: {type(exc).__name__}: {exc}")

    if actual != expected:
        module.fail_json(
            msg="TrueNAS TLS fingerprint mismatch. Authentication was not attempted.",
            expected_tls_sha256=colonize(expected),
            actual_tls_sha256=colonize(actual),
        )

    provider_payload = {
        "type": "B2",
        "account": module.params["b2_account_id"],
        "key": module.params["b2_application_key"],
    }

    changed = False
    writes_attempted = False
    operations = []

    try:
        with Client(
            module.params["host"],
            module.params["port"],
            module.params["endpoint"],
            module.params["timeout"],
        ) as client:
            auth = login_with_retry(client, module.params["username"], module.params["api_key"])
            if not isinstance(auth, dict) or auth.get("response_type") != "SUCCESS":
                module.fail_json(
                    msg="TrueNAS API authentication did not succeed.",
                    response_type=auth.get("response_type") if isinstance(auth, dict) else None,
                )

            verification = client.call("cloudsync.credentials.verify", [provider_payload])
            if not isinstance(verification, dict) or verification.get("valid") is not True:
                module.fail_json(
                    msg="Backblaze B2 credential verification failed.",
                    verification_error=(verification or {}).get("error") if isinstance(verification, dict) else None,
                )

            current_credentials = client.call("cloudsync.credentials.query", [[], {}]) or []
            current_tasks = client.call("cloudsync.query", [[], {}]) or []

            same_credentials = [
                item for item in current_credentials
                if item.get("name") == credential["name"]
            ]
            foreign_b2_credentials = [
                item for item in current_credentials
                if isinstance(item.get("provider"), dict)
                and item["provider"].get("type") == "B2"
                and item.get("name") != credential["name"]
            ]

            if credential_action == "CREATE":
                if same_credentials:
                    module.fail_json(msg="Cloud Sync credential appeared after plan; stop and re-plan.")
                if foreign_b2_credentials:
                    module.fail_json(
                        msg="Another B2 Cloud Sync credential appeared after plan; explicit adoption is required."
                    )
                writes_attempted = True
                client.call("cloudsync.credentials.create", [{
                    "name": credential["name"],
                    "provider": provider_payload,
                }])
                changed = True
                operations.append({"kind": "credential", "name": credential["name"], "action": "CREATE"})
                current_credentials = client.call("cloudsync.credentials.query", [[], {}]) or []
                same_credentials = [
                    item for item in current_credentials
                    if item.get("name") == credential["name"]
                ]
            else:
                if len(same_credentials) != 1:
                    module.fail_json(msg="Expected Cloud Sync credential is no longer uniquely present.")

            if len(same_credentials) != 1:
                module.fail_json(msg="Cloud Sync credential did not converge to one exact instance.")

            current_credential = same_credentials[0]
            provider = current_credential.get("provider") if isinstance(current_credential.get("provider"), dict) else {}
            if provider.get("type") != "B2":
                module.fail_json(msg="Cloud Sync credential provider changed after plan.")
            if provider.get("account") != module.params["b2_account_id"]:
                module.fail_json(
                    msg="Cloud Sync credential account identifier differs from Fleet Vault; explicit credential rotation is required."
                )

            credential_id = current_credential.get("id")
            desired_resolved = [
                desired_task(pool_name, credential, item)
                for item in desired_items
            ]

            remote_directory_observations = {}
            for desired in desired_resolved:
                remote_directory_observations[desired["description"]] = client.call(
                    "cloudsync.list_directory",
                    [{
                        "credentials": credential_id,
                        "encryption": False,
                        "filename_encryption": False,
                        "encryption_password": "",
                        "encryption_salt": "",
                        "attributes": {
                            "bucket": desired["attributes"]["bucket"],
                            "folder": desired["attributes"]["folder"],
                            "fast_list": desired["attributes"]["fast_list"],
                        },
                    }],
                )

            current_tasks = client.call("cloudsync.query", [[], {}]) or []
            for desired in desired_resolved:
                planned = plan_by_description[desired["description"]]
                action = planned["action"]
                same_description = [
                    item for item in current_tasks
                    if item.get("description") == desired["description"]
                ]
                same_path_other = [
                    item for item in current_tasks
                    if item.get("path") == desired["path"]
                    and item.get("description") != desired["description"]
                ]
                same_destination_other = [
                    item for item in current_tasks
                    if (item.get("attributes") or {}).get("bucket") == desired["attributes"]["bucket"]
                    and normalize_folder((item.get("attributes") or {}).get("folder")) == desired["attributes"]["folder"]
                    and item.get("description") != desired["description"]
                ]

                if same_path_other or same_destination_other:
                    module.fail_json(msg="Cloud Sync task ownership conflict appeared after plan.")

                if action == "CREATE":
                    if same_description:
                        module.fail_json(msg="Cloud Sync task appeared after plan; stop and re-plan.")

                    target_listing = remote_directory_observations[desired["description"]]
                    adoption_required = remote_listing_has_user_objects(target_listing)
                    if adoption_required and not desired["adopt_existing_remote_prefix"]:
                        module.fail_json(
                            msg="Remote SYNC destination contains existing user objects; explicit remote-prefix adoption is required before task creation.",
                            bucket=desired["attributes"]["bucket"],
                            folder=desired["attributes"]["folder"],
                        )

                    restore_mode = bool(
                        module.params["restore_existing_remote_on_empty_local"]
                    )
                    if adoption_required and restore_mode:
                        local_before = filesystem_file_summary(client, desired["path"])
                        restore_job_ids = []
                        restored_children = []
                        if local_before["files"] == 0:
                            writes_attempted = True
                            restored_children, restore_job_ids = restore_remote_children(
                                client,
                                desired,
                                credential_id,
                                target_listing,
                                module.params["restore_timeout"],
                            )

                        local_after = filesystem_file_summary(client, desired["path"])
                        verify_job, dry_run_job_id = run_sync_onetime(
                            client,
                            desired,
                            credential_id,
                            "PUSH",
                            "SYNC",
                            True,
                            module.params["adoption_timeout"],
                        )
                        no_changes = (
                            "There was nothing to transfer"
                            in str(verify_job.get("logs_excerpt") or "")
                        )
                        if not no_changes:
                            module.fail_json(
                                msg=(
                                    "Cloud Sync disaster-restore verification detected "
                                    "local/remote differences. The permanent PUSH+SYNC task "
                                    "was not created and B2 was not modified."
                                ),
                                task=desired["description"],
                                restore_job_ids=restore_job_ids,
                                restored_children=restored_children,
                                dry_run_job_id=dry_run_job_id,
                                permanent_task_created=False,
                            )

                        writes_attempted = True
                        client.call(
                            "cloudsync.create",
                            [task_payload(desired, credential_id)],
                        )
                        changed = True
                        operations.append({
                            "kind": "task",
                            "description": desired["description"],
                            "action": (
                                "RESTORE_ADOPT"
                                if restore_job_ids
                                else "RESTORE_ADOPT_RESUME"
                            ),
                            "restore_job_ids": restore_job_ids,
                            "restored_children": restored_children,
                            "dry_run_job_id": dry_run_job_id,
                            "dry_run_no_changes": True,
                            "local_files_before": local_before["files"],
                            "local_files_after": local_after["files"],
                            "local_bytes_after": local_after["bytes"],
                        })
                        current_tasks = client.call("cloudsync.query", [[], {}]) or []

                    else:
                        create_desired = dict(desired)
                        if adoption_required:
                            create_desired["enabled"] = False

                        writes_attempted = True
                        client.call(
                            "cloudsync.create",
                            [task_payload(create_desired, credential_id)],
                        )
                        changed = True
                        current_tasks = client.call("cloudsync.query", [[], {}]) or []
                        created_matches = [
                            item for item in current_tasks
                            if item.get("description") == desired["description"]
                        ]
                        if len(created_matches) != 1:
                            module.fail_json(
                                msg="Cloud Sync task did not converge to one exact instance after create."
                            )

                        if adoption_required:
                            no_changes, dry_run_job_id = verify_adoption_dry_run(
                                client,
                                created_matches[0]["id"],
                                module.params["adoption_timeout"],
                            )
                            if not no_changes:
                                module.fail_json(
                                    msg=(
                                        "Cloud Sync adoption dry-run detected remote/local differences. "
                                        "The newly created task remains disabled; no sync was run."
                                    ),
                                    task=desired["description"],
                                    dry_run_job_id=dry_run_job_id,
                                    task_left_disabled=True,
                                )
                            client.call(
                                "cloudsync.update",
                                [created_matches[0]["id"], task_payload(desired, credential_id)],
                            )
                            operations.append({
                                "kind": "task",
                                "description": desired["description"],
                                "action": "ADOPT",
                                "dry_run_job_id": dry_run_job_id,
                                "dry_run_no_changes": True,
                            })
                        else:
                            operations.append({
                                "kind": "task",
                                "description": desired["description"],
                                "action": "CREATE",
                            })
                        current_tasks = client.call("cloudsync.query", [[], {}]) or []

                elif action == "UPDATE":
                    if len(same_description) != 1:
                        module.fail_json(msg="Planned Cloud Sync task update no longer has one exact target.")
                    planned_observed = planned.get("observed") or {}
                    if planned_observed.get("id") != same_description[0].get("id"):
                        module.fail_json(msg="Cloud Sync task identity changed after plan.")

                    target_listing = remote_directory_observations[desired["description"]]
                    adoption_resume = (
                        desired["adopt_existing_remote_prefix"]
                        and bool(desired["enabled"])
                        and not bool(same_description[0].get("enabled"))
                        and remote_listing_has_user_objects(target_listing)
                    )
                    dry_run_job_id = None
                    if adoption_resume:
                        no_changes, dry_run_job_id = verify_adoption_dry_run(
                            client,
                            same_description[0]["id"],
                            module.params["adoption_timeout"],
                        )
                        if not no_changes:
                            module.fail_json(
                                msg=(
                                    "Cloud Sync adoption dry-run detected remote/local differences. "
                                    "The task remains disabled; no sync was run."
                                ),
                                task=desired["description"],
                                dry_run_job_id=dry_run_job_id,
                                task_left_disabled=True,
                            )

                    writes_attempted = True
                    client.call(
                        "cloudsync.update",
                        [same_description[0]["id"], task_payload(desired, credential_id)],
                    )
                    changed = True
                    operations.append({
                        "kind": "task",
                        "description": desired["description"],
                        "action": "ADOPT_ENABLE" if adoption_resume else "UPDATE",
                        "dry_run_job_id": dry_run_job_id,
                        "dry_run_no_changes": True if adoption_resume else None,
                    })
                    current_tasks = client.call("cloudsync.query", [[], {}]) or []

                else:
                    operations.append({
                        "kind": "task",
                        "description": desired["description"],
                        "action": "NOOP",
                    })

            final_credentials = client.call("cloudsync.credentials.query", [[], {}]) or []
            final_tasks = client.call("cloudsync.query", [[], {}]) or []

            final_credential = [
                item for item in final_credentials
                if item.get("name") == credential["name"]
                and (item.get("provider") or {}).get("type") == "B2"
            ]
            if len(final_credential) != 1:
                module.fail_json(msg="Post-apply Cloud Sync credential verification failed.")

            for desired in desired_resolved:
                matches = [
                    item for item in final_tasks
                    if item.get("description") == desired["description"]
                ]
                if len(matches) != 1 or not task_matches(matches[0], desired):
                    module.fail_json(
                        msg="Post-apply Cloud Sync task verification failed.",
                        task=desired["description"],
                    )

    except RpcError as exc:
        module.fail_json(
            msg=(
                "TrueNAS Cloud Sync write API returned an error after reconciliation began. "
                "No blind retry was attempted; run read-only audit/plan before any further writes."
            ),
            rpc_error=safe_rpc_error(exc),
            writes_attempted=writes_attempted,
        )
    except Exception as exc:
        module.fail_json(
            msg=(
                f"TrueNAS Cloud Sync API failure: {type(exc).__name__}: {exc}. "
                "Run read-only audit/plan before any further writes."
            ),
            writes_attempted=writes_attempted,
        )

    module.exit_json(
        changed=changed,
        result="PASS",
        operations=operations,
        b2_credentials_verified=True,
        declared_buckets_verified=True,
        secret_values_reported=False,
        tls_sha256_fingerprint=colonize(actual),
    )


if __name__ == "__main__":
    main()
