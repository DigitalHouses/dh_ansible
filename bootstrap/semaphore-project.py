#!/usr/bin/env python3
"""DigitalHouses Semaphore 2.19 project bootstrap via OFFICIAL localhost API.

Never edits Semaphore SQLite directly. Idempotent: existing names are checked
for exact identity; drift fails closed. Installs two read-only templates and
one optional Remote Commander installer and an independent PVE access audit.
Admin password supplied through a root-only file, never CLI arguments.
"""
from __future__ import annotations

import argparse
from http.cookiejar import CookieJar
import json
import os
from pathlib import Path
import re
import secrets
import stat
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request

CODE_GIT_URL = "https://github.com/DigitalHouses/dh_ansible.git"
SITE_INVENTORY_BASE = "/var/lib/ansimble/site-data"
VIEWS = ("01 · Подготовка", "02 · Развёртывание", "03 · Обслуживание", "04 · Приёмка")
# Global sortable numeric prefixes on Semaphore task names. Leave gaps of ten.
# Tuple: canonical title, unnumbered previous name, playbook, view.
# This is display/order only, not required task execution or dependency.
TEMPLATES = (
    ("10-Проверка запуска Ansible", "Проверка запуска Ansible",
     "playbooks/smoke-semaphore.yml", VIEWS[0]),
    ("20-Аудит локального runner", "Аудит локального runner",
     "playbooks/audit-runner.yml", VIEWS[0]),
    ("30-Установить Remote Desktop Commander", "Установить Remote Desktop Commander",
     "playbooks/request-runner-remote-commander.yml", VIEWS[2]),
    ("40-Проверка доступов PVE", "Проверка доступов PVE",
     "playbooks/request-pve-access-audit.yml", VIEWS[3]),
)
API_BASE = "https://127.0.0.1:3000/api"


class Blocked(RuntimeError):
    pass


def secure_file(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise Blocked("Secret file is missing or symlinked")
    info = path.stat()
    if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600:
        raise Blocked("Secret file must be 0600 and owned by invoking user")
    if not 0 < info.st_size < 65536:
        raise Blocked("Secret file size is not acceptable")
    return path.read_text().rstrip("\r\n")


class API:
    def __init__(self, base: str = API_BASE, *, ca_file="/etc/semaphore/tls.crt"):
        if base != API_BASE:
            raise Blocked("Seeder API must be localhost HTTPS, not remote")
        if not Path(ca_file).is_file() or Path(ca_file).is_symlink():
            raise Blocked("Trusted Semaphore local HTTPS certificate missing")
        self.base = base
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(CookieJar()),
            urllib.request.HTTPSHandler(context=ssl.create_default_context(cafile=ca_file)),
            urllib.request.ProxyHandler({}))
    
    def request(self, method: str, path: str, body=None):
        payload = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
        headers = {"Accept": "application/json"}
        if payload is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            self.base + path, data=payload, headers=headers, method=method)
        try:
            with self.opener.open(request, timeout=12) as response:
                data = response.read(2_000_000)
                return json.loads(data) if data else None
        except urllib.error.HTTPError as error:
            raise Blocked(f"Semaphore API rejected {method} {path} HTTP {error.code}") from None
        except (OSError, ValueError) as error:
            raise Blocked(f"Semaphore API unavailable or unexpected response for {path}") from error

    def login(self, password: str):
        self.request("POST", "/auth/login", {"auth": "admin", "password": password})


def one_named(objects, name, *, name_key="name"):
    matches = [obj for obj in objects if obj.get(name_key) == name]
    if len(matches) > 1:
        raise Blocked(f"Ambiguous duplicate Semaphore resource: {name}")
    return matches[0] if matches else None


def ensure(api, path, name, desired, *, key="name", apply=False, compare=()):
    items = api.request("GET", path)
    if not isinstance(items, list):
        raise Blocked(f"Expected list at {path}")
    existing = one_named(items, name, name_key=key)
    if existing is not None:
        if any(existing.get(field) != desired.get(field) for field in compare):
            raise Blocked(f"Existing Semaphore resource drift: {name}")
        return existing
    if not apply:
        print(f"PLAN_CREATE={name}", flush=True)
        return None
    created = api.request("POST", path.split("?")[0], desired)
    if not isinstance(created, dict) or not isinstance(created.get("id"), int):
        raise Blocked(f"Could not verify created resource: {name}")
    print(f"CREATED={name}", flush=True)
    return created



def ensure_template(api, path, title, legacy_title, desired, *, apply=False):
    """Create numbered templates or rename one exact, previously seeded template.

    Keep the existing template ID, task history and configuration. Refuse drift,
    or combined old/new state. No duplicate migration.
    """
    items = api.request("GET", path)
    if not isinstance(items, list):
        raise Blocked(f"Expected template list at {path}")
    known_names = (title, legacy_title)
    matches = [obj for obj in items if obj.get("name") in known_names]
    if len(matches) > 1:
        raise Blocked(f"Conflicting old/new Semaphore template names: {title}")
    if not matches:
        # An unknown name for the same playbook must never create a duplicate.
        if any(item.get("playbook") == desired["playbook"]
               and item.get("project_id") == desired["project_id"]
               for item in items):
            raise Blocked(f"Unexpected Semaphore template name for {title}; "
                          "rename existing template manually before seeding")
        return ensure(api, path, title, desired, apply=apply,
                      compare=("playbook", "app", "inventory_id", "repository_id",
                               "environment_id", "view_id", "autorun"))
    current = matches[0]
    previous_title = current.get("name")
    # Only a template whose entire effective installation contract still matches
    # our originally seeded resource is eligible for an in-place rename.
    required = ("project_id", "playbook", "arguments", "type", "app",
                "inventory_id", "repository_id", "environment_id", "view_id",
                "autorun", "allow_override_args_in_task")
    if any(current.get(field) != desired.get(field) for field in required):
        raise Blocked(f"Existing Semaphore resource drift: {current.get('name')}")
    if current.get("name") == title:
        return current
    template_id = current.get("id")
    if type(template_id) is not int or template_id <= 0:
        raise Blocked(f"Invalid Semaphore template ID for rename: {previous_title}")
    if not apply:
        print(f"PLAN_RENAME={previous_title} => {title} id={template_id}", flush=True)
        return current

    prefix = path.split("?")[0]
    detail = api.request("GET", f"{prefix}/{template_id}")
    if not isinstance(detail, dict) or detail.get("id") != template_id:
        raise Blocked("Semaphore template details mismatch; rename cancelled")
    if detail.get("name") != previous_title or any(
            detail.get(field) != desired.get(field) for field in required):
        raise Blocked("Semaphore template drift before rename; cancelled")
    # Use the API's own complete resource representation, changing name ONLY.
    # All other task/template options and the identifier remain unchanged.
    payload = dict(detail)
    payload["name"] = title
    api.request("PUT", f"{prefix}/{template_id}", payload)

    after = api.request("GET", path)
    if not isinstance(after, list):
        raise Blocked("Cannot verify Semaphore template rename")
    matches_after = [item for item in after
                     if item.get("name") in known_names]
    if (len(matches_after) != 1
            or matches_after[0].get("name") != title
            or matches_after[0].get("id") != template_id
            or any(matches_after[0].get(k) != detail.get(k)
                   for k in detail if k != "name" and k in matches_after[0])):
        raise Blocked("Semaphore template post-rename verification failed")
    print(f"RENAMED_TEMPLATE={previous_title} => {title} id={template_id}",
          flush=True)
    return matches_after[0]


def provision(api, site: str, project_title: str, apply: bool):
    project = ensure(api, "/projects", project_title,
                     {"name": project_title, "max_parallel_tasks": 1, "alert": False, "demo": False},
                     apply=apply)
    if project is None:
        return {"state": "PLAN_ONLY"}
    pid = project["id"]
    prefix = f"/project/{pid}"
    # The site runner never receives credentials for private Fleet Detail.
    public_key = ensure(api, prefix + "/keys?sort=name&order=asc",
                        "Public Git (none)",
                        {"name": "Public Git (none)", "type": "none",
                         "project_id": pid},
                        apply=apply, compare=("type",))
    if public_key is None:
        return {"state": "PLAN_ONLY"}
    local_key = ensure(api, prefix + "/keys?sort=name&order=asc",
                       "Runner inventory (local-only)",
                       {"name": "Runner inventory (local-only)",
                        "type": "login_password", "project_id": pid,
                        "login_password": {"login": "dh-local-only",
                                           "password": secrets.token_urlsafe(48)}},
                       apply=apply, compare=("type",))
    if local_key is None:
        return {"state": "PLAN_ONLY"}
    code_repo = ensure(api, prefix + "/repositories?sort=name&order=asc",
                       "DigitalHouses code",
                       {"name": "DigitalHouses code", "project_id": pid,
                        "git_url": CODE_GIT_URL, "git_branch": "main",
                        "ssh_key_id": public_key["id"]},
                       apply=apply,
                       compare=("git_url", "git_branch", "ssh_key_id"))
    if code_repo is None:
        return {"state": "PLAN_ONLY"}
    local_inventory = ensure(api, prefix + "/inventory?sort=name&order=asc",
                             "Runner (local)",
                             {"name": "Runner (local)", "project_id": pid,
                              "type": "file",
                              "inventory": f"{SITE_INVENTORY_BASE}/{site}/hosts.yml",
                              "ssh_key_id": local_key["id"]},
                             apply=apply,
                             compare=("type", "inventory", "ssh_key_id",
                                      "repository_id"))
    if local_inventory is None:
        return {"state": "PLAN_ONLY"}
    env = ensure(api, prefix + "/environment?sort=name&order=asc", "Empty",
                 {"name": "Empty", "project_id": pid, "json": "{}", "env": "{}"},
                 apply=apply)
    if env is None:
        return {"state": "PLAN_ONLY"}
    views = {}
    for n, title in enumerate(VIEWS):
        views[title] = ensure(api, prefix + "/views", title,
                              {"title": title, "project_id": pid, "position": n},
                              key="title", apply=apply)
    if not all(views.values()):
        return {"state": "PLAN_ONLY"}
    for title, legacy_title, playbook, view in TEMPLATES:
        ensure_template(api, prefix + "/templates?sort=name&order=asc",
                        title, legacy_title,
               {"name": title, "project_id": pid, "app": "ansible",
                "playbook": playbook, "arguments": "[]", "type": "",
                "inventory_id": local_inventory["id"], "repository_id": code_repo["id"],
                "environment_id": env["id"], "view_id": views[view]["id"],
                "allow_override_args_in_task": False, "autorun": False},
               apply=apply)
    return {"state": "APPLIED" if apply else "PLAN_ONLY", "project_id": pid}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site", required=True)
    parser.add_argument("--project-title", required=True)
    parser.add_argument("--admin-password-file", required=True, type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    if not re.fullmatch(r"[a-z][a-z0-9_-]{0,30}", args.site):
        raise Blocked("Invalid site ID")
    if not 1 <= len(args.project_title) <= 100:
        raise Blocked("Invalid project title")
    if os.geteuid() != 0:
        raise Blocked("Run as root inside CT runner, not on PVE")
    if Path("/etc/pve/user.cfg").exists():
        raise Blocked("Never run Seeder on PVE host")
    if os.uname().nodename != f"ansible-{args.site}":
        raise Blocked("Unexpected guest hostname")
    password = secure_file(args.admin_password_file)
    api = API()
    api.login(password)
    del password
    result = provision(api, args.site, args.project_title, args.apply)
    print(f"SEMAPHORE_PROJECT={result['state']}", flush=True)
    print("NO_INFRASTRUCTURE_TASKS_LAUNCHED=YES", flush=True)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Blocked as error:
        print(f"BLOCKED: {error}", file=sys.stderr)
        sys.exit(2)
