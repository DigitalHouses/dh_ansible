#!/usr/bin/env python3
"""Task 05 pilot: save only supplied credentials in native Semaphore 2.19 Key Store / Variable Groups.

No alternate secret store. The two DH_TEST_ fields are deliberately dummy
credentials for the first E2E; do not use real infrastructure passwords yet.
"""
import argparse
import os
from pathlib import Path
import re
import runpy

API_MODULE = runpy.run_path(
    str(Path(__file__).resolve().parents[1] / "bootstrap" / "semaphore-project.py"),
    run_name="dh_semaphore_api")
API = API_MODULE["API"]
Blocked = API_MODULE["Blocked"]
secure_file = API_MODULE["secure_file"]
valid_catalog_token = API_MODULE["valid_catalog_token"]
CATALOG_TOKEN_PATH = Path("/root/dh-semaphore-catalog-api-token")
TEST_KEY_NAME = "DH TEST ONLY - Login Password"
TEST_VAR_GROUP = "DH TEST ONLY - Secret Variables"
TEST_VAR_NAME = "DH_TEST_ONLY_MQTT_SECRET"
TEST_LOGIN = "dh-test-user"


def exact_named(items, name):
    if not isinstance(items, list):
        raise Blocked("SEMAPHORE_INVALID_RESOURCE_LIST")
    found = [item for item in items if isinstance(item, dict) and item.get("name") == name]
    if len(found) > 1:
        raise Blocked("SEMAPHORE_DUPLICATE_RESOURCE: " + name)
    return found[0] if found else None


def positive_id(obj):
    result = obj.get("id") if isinstance(obj, dict) else None
    if type(result) is not int or result < 1:
        raise Blocked("SEMAPHORE_INVALID_RESOURCE_ID")
    return result


def supplied_environment(name):
    value = os.environ.get(name, "")
    if not isinstance(value, str) or len(value) > 8192 or "\x00" in value:
        raise Blocked("CREDENTIAL_INVALID_VALUE: " + name)
    # Missing or empty survey input is always KEEP (never delete).
    return value


def project_id_for_site(api, site):
    projects = api.request("GET", "/projects")
    project = exact_named(projects, site)
    if project is None:
        raise Blocked("SEMAPHORE_SITE_PROJECT_MISSING")
    return positive_id(project)


def verify_key_identity(api, prefix, key_id):
    key = api.request("GET", prefix + "/keys/" + str(key_id))
    if (not isinstance(key, dict) or key.get("id") != key_id
            or key.get("name") != TEST_KEY_NAME
            or key.get("type") != "login_password"):
        raise Blocked("KEY_STORE_RESOURCE_IDENTITY_MISMATCH")


def save_login_password(api, prefix, project_id, password):
    path = prefix + "/keys?sort=name&order=asc"
    existing = exact_named(api.request("GET", path), TEST_KEY_NAME)
    body = {
        "project_id": project_id,
        "name": TEST_KEY_NAME,
        "type": "login_password",
        "login_password": {"login": TEST_LOGIN, "password": password},
    }
    if existing is None:
        result = api.request("POST", prefix + "/keys", body)
        key_id = positive_id(result)
        outcome = "CREATED"
    else:
        if (existing.get("type") != "login_password"
                or existing.get("project_id") not in (None, project_id)):
            raise Blocked("KEY_STORE_EXISTING_KEY_MISMATCH")
        key_id = positive_id(existing)
        body.update({"id": key_id, "override_secret": True})
        api.request("PUT", prefix + "/keys/" + str(key_id), body)
        outcome = "UPDATED"
    verify_key_identity(api, prefix, key_id)
    return outcome


def verify_group_identity(env, project_id):
    if (not isinstance(env, dict) or env.get("name") != TEST_VAR_GROUP
            or env.get("project_id") != project_id
            or not isinstance(env.get("json"), str)
            or not isinstance(env.get("env"), (str, type(None)))):
        raise Blocked("VARIABLE_GROUP_IDENTITY_MISMATCH")


def save_variable_secret(api, prefix, project_id, secret):
    path = prefix + "/environment?sort=name&order=asc"
    existing = exact_named(api.request("GET", path), TEST_VAR_GROUP)
    if existing is None:
        body = {
            "name": TEST_VAR_GROUP,
            "project_id": project_id,
            "json": "{}",
            "env": "{}",
            "secrets": [{
                "name": TEST_VAR_NAME, "type": "env",
                "operation": "create", "secret": secret,
            }],
        }
        created = api.request("POST", prefix + "/environment", body)
        group_id = positive_id(created)
        outcome = "CREATED"
    else:
        group_id = positive_id(existing)
        current = api.request("GET", prefix + "/environment/" + str(group_id))
        verify_group_identity(current, project_id)
        entries = current.get("secrets") or []
        item = exact_named(entries, TEST_VAR_NAME)
        if item is None:
            spec = {
                "name": TEST_VAR_NAME, "type": "env",
                "operation": "create", "secret": secret,
            }
        else:
            if item.get("type") != "env":
                raise Blocked("VARIABLE_SECRET_EXISTING_TYPE_MISMATCH")
            spec = {
                "id": positive_id(item), "name": TEST_VAR_NAME,
                "type": "env", "operation": "update", "secret": secret,
            }
        # Preserve everything else; Semaphore applies only the listed secret operation.
        body = {
            "id": group_id, "name": current["name"],
            "project_id": project_id,
            "json": current["json"], "env": current.get("env"),
            "secrets": [spec],
        }
        api.request("PUT", prefix + "/environment/" + str(group_id), body)
        outcome = "UPDATED"
    after = api.request("GET", prefix + "/environment/" + str(group_id))
    verify_group_identity(after, project_id)
    test = exact_named(after.get("secrets") or [], TEST_VAR_NAME)
    if test is None or test.get("type") != "env":
        raise Blocked("VARIABLE_SECRET_METADATA_NOT_SAVED")
    # No secret value is returned or logged. End-to-end use is checked separately.
    return outcome


def enroll(api, site, key_password, variable_password):
    if not key_password and not variable_password:
        return {"key_store": "KEEP", "variable_group": "KEEP"}
    project_id = project_id_for_site(api, site)
    prefix = "/project/" + str(project_id)
    result = {"key_store": "KEEP", "variable_group": "KEEP"}
    if key_password:
        result["key_store"] = save_login_password(api, prefix, project_id, key_password)
    if variable_password:
        result["variable_group"] = save_variable_secret(api, prefix, project_id, variable_password)
    return result


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
    key_password = supplied_environment("DH_TEST_KEYSTORE_PASSWORD")
    variable_password = supplied_environment("DH_TEST_VARIABLE_SECRET")
    # Nothing entered -> no API writes and no existing key altered.
    if not key_password and not variable_password:
        print("OBTAIN_CREDENTIALS_TEST=NO_CHANGES")
        return
    token = secure_file(CATALOG_TOKEN_PATH)
    if not valid_catalog_token(token):
        raise Blocked("SEMAPHORE_API_CREDENTIAL_UNAVAILABLE")
    api = API(token=token)
    del token
    result = enroll(api, site, key_password, variable_password)
    print("OBTAIN_CREDENTIALS_TEST=PASS")
    print("KEY_STORE=" + result["key_store"])
    print("VARIABLE_GROUP=" + result["variable_group"])
    print("NOTE=TEST_VALUES_ONLY_NO_AUTHENTICATION_CHECK")


if __name__ == "__main__":
    try:
        main()
    except Blocked as error:
        # Never log API responses, secret values, or HTTP request bodies.
        print("OBTAIN_CREDENTIALS_TEST=BLOCKED " + str(error).splitlines()[0])
        raise SystemExit(2) from None
