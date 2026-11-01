#!/usr/bin/env python3
"""Task 05: write supplied site credentials to native Semaphore 2.19.16 Variable Groups.

No secondary secret store. Six optional environment-backed Survey values
are declared in catalog/site-credentials.json and remain absent/KEEP when blank.
Git/PVE SSH identities are separately generated/commissioned into native Key Store.
"""
import argparse
import json
import os
from pathlib import Path
import re
import runpy

ROOT = Path(__file__).resolve().parents[1]
api_module = runpy.run_path(str(ROOT / "bootstrap/semaphore-project.py"),
                            run_name="dh_semaphore_api")
API = api_module["API"]
Blocked = api_module["Blocked"]
secure_file = api_module["secure_file"]
valid_catalog_token = api_module["valid_catalog_token"]
CATALOG_TOKEN_PATH = Path("/root/dh-semaphore-catalog-api-token")
REGISTRY_PATH = ROOT / "catalog/site-credentials.json"
SITE_VARIABLE_GROUP = "DigitalHouses Site Credentials"


def exact_named(items, name):
    if not isinstance(items, list):
        raise Blocked("SEMAPHORE_INVALID_RESOURCE_LIST")
    found = [item for item in items if isinstance(item, dict)
             and item.get("name") == name]
    if len(found) > 1:
        raise Blocked("SEMAPHORE_DUPLICATE_RESOURCE: " + name)
    return found[0] if found else None


def positive_id(obj):
    result = obj.get("id") if isinstance(obj, dict) else None
    if type(result) is not int or result < 1:
        raise Blocked("SEMAPHORE_INVALID_RESOURCE_ID")
    return result


def registry_fields():
    try:
        manifest = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise Blocked("CREDENTIAL_REGISTRY_INVALID") from exc
    if manifest.get("schema") != 1 or not isinstance(manifest.get("manual"), list):
        raise Blocked("CREDENTIAL_REGISTRY_SCHEMA_INVALID")
    fields = manifest["manual"]
    if len(fields) != 6 or len({f.get("id") for f in fields}) != 6:
        raise Blocked("CREDENTIAL_REGISTRY_UNEXPECTED_FIELDS")
    if len({f.get("survey_env") for f in fields}) != len(fields):
        raise Blocked("CREDENTIAL_REGISTRY_DUPLICATE_SURVEY_ENV")
    if len({f.get("runtime_name") for f in fields}) != len(fields):
        raise Blocked("CREDENTIAL_REGISTRY_DUPLICATE_RUNTIME_NAME")
    for field in fields:
        if (field.get("input") not in ("secret", "text")
                or field.get("store") not in ("variable_group_secret",
                                              "variable_group_nonsecret")
                or not re.fullmatch(r"DH_INPUT_[A-Z0-9_]{4,70}",
                                    str(field.get("survey_env")))
                or not re.fullmatch(r"DH_[A-Z0-9_]{4,70}",
                                    str(field.get("runtime_name")))):
            raise Blocked("CREDENTIAL_REGISTRY_FIELD_INVALID")
    return fields


def supplied_environment(name):
    value = os.environ.get(name, "")
    if not isinstance(value, str) or len(value) > 8192 or "\x00" in value:
        raise Blocked("CREDENTIAL_INVALID_INPUT: " + name)
    # Empty means KEEP, including at first use. Task 06 reports missing values.
    return value


def project_id_for_site(api, site):
    project = exact_named(api.request("GET", "/projects"), site)
    if project is None:
        raise Blocked("SEMAPHORE_SITE_PROJECT_MISSING")
    return positive_id(project)


def validate_existing_group(group, project_id):
    if (not isinstance(group, dict)
            or group.get("name") != SITE_VARIABLE_GROUP
            or group.get("project_id") != project_id
            or not isinstance(group.get("json"), str)
            or group.get("env") is not None
            and not isinstance(group.get("env"), str)
            or group.get("secret_storage_id") not in (None, 0)
            or group.get("sync_enabled") is True):
        raise Blocked("SITE_CREDENTIAL_GROUP_IDENTITY_MISMATCH")


def nonsecret_env_json(value):
    try:
        env = json.loads(value or "{}")
    except ValueError as exc:
        raise Blocked("SITE_CREDENTIAL_GROUP_ENV_INVALID") from exc
    if not isinstance(env, dict) or any(
            not isinstance(k, str) or not isinstance(v, str)
            for k, v in env.items()):
        raise Blocked("SITE_CREDENTIAL_GROUP_ENV_INVALID")
    return env


def save_fields(api, site, supplied, fields):
    if not supplied:
        return {f["id"]: "KEEP" for f in fields}
    project_id = project_id_for_site(api, site)
    prefix = "/project/" + str(project_id)
    group_path = prefix + "/environment"
    groups = api.request("GET", group_path + "?sort=name&order=asc")
    existing = exact_named(groups, SITE_VARIABLE_GROUP)
    if existing is None:
        current = None
        nonsecret = {}
        secrets_existing = []
        group_id = None
    else:
        group_id = positive_id(existing)
        current = api.request("GET", group_path + "/" + str(group_id))
        validate_existing_group(current, project_id)
        nonsecret = nonsecret_env_json(current.get("env"))
        secrets_existing = current.get("secrets") or []
        if not isinstance(secrets_existing, list):
            raise Blocked("SITE_CREDENTIAL_GROUP_SECRETS_INVALID")
    specs = []
    actions = {}
    for field in fields:
        credential_id = field["id"]
        if credential_id not in supplied:
            actions[credential_id] = "KEEP"
            continue
        value = supplied[credential_id]
        name = field["runtime_name"]
        if field["store"] == "variable_group_nonsecret":
            nonsecret[name] = value
            actions[credential_id] = "UPDATED" if current is not None else "CREATED"
            continue
        previous = exact_named(secrets_existing, name)
        if previous is None:
            op = "create"
        else:
            if previous.get("type") != "env":
                raise Blocked("SITE_CREDENTIAL_SECRET_TYPE_MISMATCH: " + credential_id)
            op = "update"
        record = {"name": name, "type": "env",
                  "operation": op, "secret": value}
        if op == "update":
            record["id"] = positive_id(previous)
        specs.append(record)
        actions[credential_id] = "CREATED" if op == "create" else "UPDATED"
    if current is None:
        body = {"project_id": project_id, "name": SITE_VARIABLE_GROUP,
                "json": "{}", "env": json.dumps(nonsecret),
                "secrets": specs}
        created = api.request("POST", group_path, body)
        group_id = positive_id(created)
    else:
        body = {"id": group_id, "project_id": project_id,
                "name": SITE_VARIABLE_GROUP,
                "json": current["json"], "env": json.dumps(nonsecret),
                "secrets": specs}
        api.request("PUT", group_path + "/" + str(group_id), body)
    after = api.request("GET", group_path + "/" + str(group_id))
    validate_existing_group(after, project_id)
    actual = nonsecret_env_json(after.get("env"))
    for field in fields:
        credential_id = field["id"]
        if credential_id not in supplied:
            continue
        name = field["runtime_name"]
        if field["store"] == "variable_group_nonsecret":
            if actual.get(name) != supplied[credential_id]:
                raise Blocked("SITE_CREDENTIAL_METADATA_NOT_SAVED: " + credential_id)
        else:
            item = exact_named(after.get("secrets") or [], name)
            if item is None or item.get("type") != "env":
                raise Blocked("SITE_CREDENTIAL_METADATA_NOT_SAVED: " + credential_id)
    return actions


def enroll(api, site, values):
    fields = registry_fields()
    if not isinstance(values, dict) or set(values) - {f["id"] for f in fields}:
        raise Blocked("CREDENTIAL_INPUT_SET_INVALID")
    safe = {}
    for field in fields:
        if field["id"] not in values:
            continue
        value = values[field["id"]]
        if not isinstance(value, str) or not value or len(value) > 8192 or "\x00" in value:
            raise Blocked("CREDENTIAL_INPUT_VALUE_INVALID: " + field["id"])
        safe[field["id"]] = value
    return save_fields(api, site, safe, fields)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site", required=True)
    args = parser.parse_args()
    site = args.site
    if not re.fullmatch(r"[a-z][a-z0-9]*(-[a-z0-9]+)+", site):
        raise Blocked("SITE_ID_INVALID")
    if os.geteuid() != 0 or Path("/etc/pve/user.cfg").exists():
        raise Blocked("RUNNER_LOCAL_ROOT_REQUIRED")
    if os.uname().nodename != "ansible-" + site:
        raise Blocked("SITE_RUNNER_HOSTNAME_MISMATCH")
    fields = registry_fields()
    values = {}
    for field in fields:
        value = supplied_environment(field["survey_env"])
        if value:
            values[field["id"]] = value
    if not values:
        print("OBTAIN_CREDENTIALS=NO_CHANGES")
        return
    token = secure_file(CATALOG_TOKEN_PATH)
    if not valid_catalog_token(token):
        raise Blocked("SEMAPHORE_API_CREDENTIAL_UNAVAILABLE")
    api = API(token=token)
    del token
    actions = enroll(api, site, values)
    print("OBTAIN_CREDENTIALS=PASS")
    for field in fields:
        print("FIELD=" + field["id"] + " STATUS=" + actions[field["id"]])
    print("VALIDATION=NOT_CHECKED_RUN_TASK_06")


if __name__ == "__main__":
    try:
        main()
    except Blocked as error:
        print("OBTAIN_CREDENTIALS=BLOCKED " + str(error).splitlines()[0])
        raise SystemExit(2) from None
    except Exception:
        # Never expose request bodies, secret values or unexpected library errors.
        print("OBTAIN_CREDENTIALS=BLOCKED INTERNAL_ERROR_NO_SECRET_DETAILS")
        raise SystemExit(2) from None
