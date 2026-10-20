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
SITE_INVENTORY_BASE = "/var/lib/ansible/site-data"
VIEWS = ("01 · Подготовка", "02 · Развёртывание", "03 · Обслуживание", "04 · Приёмка")
# Global sortable numeric prefixes on Semaphore task names. Leave gaps of ten.
# Tuple: canonical title, unnumbered previous name, playbook, view.
# This is display/order only, not required task execution or dependency.
BOOTSTRAP_TEMPLATES = (
    ("00-Обновить список задач", "Обновить список задач",
     "playbooks/request-semaphore-catalog-sync.yml", VIEWS[0]),
)
# One-time controlled upgrade from the accepted four-task Semaphore bootstrap.
# Migrate in descending numeric order because new titles overlap old titles.
# (old title, new title, exact playbook, expected old view, new view)
LEGACY_MIGRATIONS = (
    ("40-Проверка доступов PVE", "50-Проверка доступов PVE",
     "playbooks/request-pve-access-audit.yml", VIEWS[3], VIEWS[3]),
    ("30-Установить Remote Desktop Commander", "40-Установить Remote Desktop Commander",
     "playbooks/request-runner-remote-commander.yml", VIEWS[2], VIEWS[2]),
    ("20-Аудит локального runner", "30-Аудит локального runner",
     "playbooks/audit-runner.yml", VIEWS[0], VIEWS[3]),
    ("10-Проверка запуска Ansible", "20-Проверка запуска Ansible",
     "playbooks/smoke-semaphore.yml", VIEWS[0], VIEWS[3]),
)
CATALOG_PATH = "catalog/semaphore-tasks.json"
CATALOG_SOURCE = "DigitalHouses/dh_ansible"
CATALOG_TOKEN_PATH = Path("/root/dh-semaphore-catalog-api-token")
MANAGED_DESCRIPTION = "DigitalHouses · управляется каталогом dh_ansible"

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
    def __init__(self, base: str = API_BASE, *, ca_file="/etc/semaphore/tls.crt", token=None):
        if base != API_BASE:
            raise Blocked("Seeder API must be localhost HTTPS, not remote")
        if not Path(ca_file).is_file() or Path(ca_file).is_symlink():
            raise Blocked("Trusted Semaphore local HTTPS certificate missing")
        self.base = base
        self.token = token
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(CookieJar()),
            urllib.request.HTTPSHandler(context=ssl.create_default_context(cafile=ca_file)),
            urllib.request.ProxyHandler({}))
    
    def request(self, method: str, path: str, body=None):
        payload = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
        headers = {"Accept": "application/json"}
        if self.token is not None:
            headers["Authorization"] = "Bearer " + self.token
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


def canonical_template_field(field, value):
    """Semaphore 2.19 can omit default fields in template list API responses.

    Treat only semantically identical safe defaults as equal; never accept
    true autorun or enabled argument override as an omitted false default.
    """
    if field == "type" and value in (None, "", "task"):
        return "task"
    if field in ("autorun", "allow_override_args_in_task") and value is None:
        return False
    return value


def template_equal(field, actual, desired):
    return (canonical_template_field(field, actual)
            == canonical_template_field(field, desired))


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
    drift_fields = [field for field in required
                    if not template_equal(field, current.get(field), desired.get(field))]
    if drift_fields:
        raise Blocked(f"Existing Semaphore resource drift: {current.get('name')} "
                      f"fields={','.join(drift_fields)}")
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
            not template_equal(field, detail.get(field), desired.get(field)) for field in required):
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


def provision(api, site: str, project_title: str, apply: bool, *, templates=None):
    if templates is None:
        templates = BOOTSTRAP_TEMPLATES
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
    for title, legacy_title, playbook, view in templates:
        ensure_template(api, prefix + "/templates?sort=name&order=asc",
                        title, legacy_title,
               {"name": title, "project_id": pid, "app": "ansible",
                "description": MANAGED_DESCRIPTION,
                "playbook": playbook, "arguments": "[]", "type": "",
                "inventory_id": local_inventory["id"], "repository_id": code_repo["id"],
                "environment_id": env["id"], "view_id": views[view]["id"],
                "allow_override_args_in_task": False, "autorun": False},
               apply=apply)
    return {"state": "APPLIED" if apply else "PLAN_ONLY", "project_id": pid}




def create_catalog_api_token_once(api):
    """Keep a revocable Semaphore API token root-only; UI password may rotate."""
    if CATALOG_TOKEN_PATH.exists() or CATALOG_TOKEN_PATH.is_symlink():
        token = secure_file(CATALOG_TOKEN_PATH)
        if not re.fullmatch(r"[A-Za-z0-9_-]{20,256}", token):
            raise Blocked("Existing Semaphore catalog token invalid")
        return
    answer = api.request("POST", "/user/tokens")
    token = answer.get("id") if isinstance(answer, dict) else None
    if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{20,256}", token):
        raise Blocked("Semaphore API returned unexpected token format")
    fd = os.open(CATALOG_TOKEN_PATH, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="ascii") as out:
            out.write(token + "\n")
    except BaseException:
        CATALOG_TOKEN_PATH.unlink(missing_ok=True)
        raise
    if secure_file(CATALOG_TOKEN_PATH) != token:
        raise Blocked("Could not verify root-only Semaphore catalog token")
    print("SEMAPHORE_CATALOG_API_TOKEN=ROOT_ONLY_CREATED", flush=True)


def read_public_catalog():
    """Read a validated, immutable snapshot of the public catalog on each run."""
    def get_json(url: str, size: int = 128_000):
        request = urllib.request.Request(url, headers={
            "User-Agent": "DigitalHouses-Semaphore-Catalog/1",
            "Accept": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                if response.status != 200:
                    raise Blocked("Public catalog HTTP status is not 200")
                data = response.read(size + 1)
        except (OSError, urllib.error.URLError) as error:
            raise Blocked("Cannot read public task catalog") from error
        if len(data) > size:
            raise Blocked("Public catalog exceeds size limit")
        try:
            return json.loads(data)
        except (ValueError, UnicodeDecodeError) as error:
            raise Blocked("Invalid public catalog JSON") from error

    head = get_json("https://api.github.com/repos/" + CATALOG_SOURCE + "/commits/main")
    sha = head.get("sha") if isinstance(head, dict) else None
    if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise Blocked("Invalid public catalog source commit")
    raw = "https://raw.githubusercontent.com/" + CATALOG_SOURCE + "/" + sha + "/"
    catalog = get_json(raw + CATALOG_PATH)
    if not isinstance(catalog, dict) or catalog.get("schema") != 1:
        raise Blocked("Unsupported public task catalog schema")
    entries = catalog.get("tasks")
    if not isinstance(entries, list) or not 1 <= len(entries) <= 90:
        raise Blocked("Public task catalog has invalid task count")
    seen = set()
    tasks = []
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"title", "playbook", "view"}:
            raise Blocked("Unexpected public task catalog fields")
        title, playbook, view = (entry[k] for k in ("title", "playbook", "view"))
        if (not isinstance(title, str) or
                not re.fullmatch(r"[0-9]{2}-[^\r\n]{3,90}", title)):
            raise Blocked("Invalid public catalog task title")
        if title in seen:
            raise Blocked("Duplicate public catalog title")
        if not isinstance(playbook, str) or not re.fullmatch(
                r"playbooks/[a-z0-9-]+\.yml", playbook):
            raise Blocked("Invalid public catalog playbook path")
        if view not in VIEWS:
            raise Blocked("Unknown public catalog view")
        seen.add(title)
        # Playbooks must exist in the same public immutable source revision.
        req = urllib.request.Request(raw + playbook, headers={
            "User-Agent": "DigitalHouses-Semaphore-Catalog/1"})
        try:
            with urllib.request.urlopen(req, timeout=20) as response:
                if response.status != 200 or len(response.read(100_001)) > 100_000:
                    raise Blocked("Catalog playbook missing or oversized")
        except (OSError, urllib.error.URLError) as error:
            raise Blocked("Catalog playbook not available: " + playbook) from error
        tasks.append((title, title, playbook, view))
    bootstrap = BOOTSTRAP_TEMPLATES[0]
    if tasks[0][:1] != bootstrap[:1] or tasks[0][2:] != bootstrap[2:]:
        raise Blocked("Catalog cannot change the bootstrap recovery task")
    print("CATALOG_SOURCE_SHA=" + sha, flush=True)
    return tasks


def sync_existing_template(api, path, desired, *, apply=False):
    """Modify only a known catalog-owned template, preserving ID and history."""
    items = api.request("GET", path)
    if not isinstance(items, list):
        raise Blocked("Cannot read catalog templates")
    title = desired["name"]
    matches = [item for item in items if item.get("name") == title]
    if len(matches) > 1:
        raise Blocked("Duplicate catalog template name: " + title)
    if not matches:
        if any(item.get("playbook") == desired["playbook"] for item in items):
            raise Blocked("Catalog playbook already bound to another task")
        if not apply:
            print("PLAN_CREATE=" + title, flush=True)
            return
        ensure(api, path, title, desired, apply=True,
               compare=("playbook", "repository_id", "inventory_id"))
        print("CATALOG_CREATED=" + title, flush=True)
        return
    current = matches[0]
    immutable = ("project_id", "app", "repository_id", "inventory_id",
                 "environment_id", "arguments", "type",
                 "autorun", "allow_override_args_in_task")
    if any(not template_equal(k, current.get(k), desired.get(k)) for k in immutable):
        raise Blocked("Catalog template safety drift: " + title)
    # The bootstrap template can be imported from old seeder versions only by
    # a deliberately reviewed migration. On fresh installs it is managed.
    if current.get("description") != MANAGED_DESCRIPTION:
        raise Blocked("Template is not catalog-owned: " + title)
    allowed_change = ("playbook", "view_id")
    changed = [k for k in allowed_change if current.get(k) != desired.get(k)]
    if not changed:
        if apply:
            print("CATALOG_UNCHANGED=" + title, flush=True)
        return
    if title == BOOTSTRAP_TEMPLATES[0][0]:
        raise Blocked("Never rewrite bootstrap recovery task")
    if not apply:
        print("PLAN_UPDATE=" + title, flush=True)
        return
    template_id = current.get("id")
    if type(template_id) is not int or template_id <= 0:
        raise Blocked("Invalid catalog template ID")
    prefix = path.split("?")[0]
    details = api.request("GET", prefix + "/" + str(template_id))
    if details.get("id") != template_id or any(
            not template_equal(k, details.get(k), current.get(k)) for k in immutable + allowed_change):
        raise Blocked("Catalog template changed concurrently")
    payload = dict(details)
    payload.update({k: desired[k] for k in allowed_change})
    api.request("PUT", prefix + "/" + str(template_id), payload)
    verify = api.request("GET", prefix + "/" + str(template_id))
    if verify.get("id") != template_id or any(
            verify.get(k) != desired[k] for k in allowed_change):
        raise Blocked("Catalog template update could not be verified")
    print("CATALOG_UPDATED=" + title, flush=True)


def synchronize_catalog(api, site: str, project_title: str, *, apply: bool):
    """Synchronize templates of an existing, exactly identified site project."""
    tasks = read_public_catalog()
    # Bootstrap seeding establishes trusted keys/inventory/views and validates
    # fixed resource identity before any dynamic template changes.
    provision(api, site, project_title, apply, templates=BOOTSTRAP_TEMPLATES)
    projects = api.request("GET", "/projects")
    named = one_named(projects, project_title)
    if not named:
        raise Blocked("Existing site project is missing")
    prefix = f"/project/{named['id']}"
    views = api.request("GET", prefix + "/views")
    repositories = api.request("GET", prefix + "/repositories?sort=name&order=asc")
    inventory = api.request("GET", prefix + "/inventory?sort=name&order=asc")
    environment = api.request("GET", prefix + "/environment?sort=name&order=asc")
    view_ids = {v["title"]: v["id"] for v in views if v.get("title") in VIEWS}
    repo = one_named(repositories, "DigitalHouses code")
    inv = one_named(inventory, "Runner (local)")
    env = one_named(environment, "Empty")
    if len(view_ids) != len(VIEWS) or not all((repo, inv, env)):
        raise Blocked("Site project prerequisite is missing")
    path = prefix + "/templates?sort=name&order=asc"
    # Check the complete catalog first (fail closed before any writes).
    for title, _, playbook, view in tasks:
        desired = {"name": title, "project_id": named["id"], "app": "ansible",
                   "description": MANAGED_DESCRIPTION, "playbook": playbook,
                   "arguments": "[]", "type": "",
                   "inventory_id": inv["id"], "repository_id": repo["id"],
                   "environment_id": env["id"], "view_id": view_ids[view],
                   "allow_override_args_in_task": False, "autorun": False}
        sync_existing_template(api, path, desired, apply=False)
    if apply:
        for title, _, playbook, view in tasks:
            desired = {"name": title, "project_id": named["id"], "app": "ansible",
                       "description": MANAGED_DESCRIPTION, "playbook": playbook,
                       "arguments": "[]", "type": "",
                       "inventory_id": inv["id"], "repository_id": repo["id"],
                       "environment_id": env["id"], "view_id": view_ids[view],
                       "allow_override_args_in_task": False, "autorun": False}
            sync_existing_template(api, path, desired, apply=True)
    return {"state": "APPLIED" if apply else "PLAN_ONLY", "project_id": named["id"]}



def migrate_accepted_four_task_catalog(api, site: str, project_title: str,
                                       *, apply: bool):
    """One-off owner-authorized migration; preserve template IDs and task history.

    Only the four known legacy templates, optionally plus the new recovery task,
    are accepted. Validate ALL tasks before any changes and rename descending
    to avoid 20/30/40 collisions. A partial prior migration is restartable.
    """
    existing_project = provision(api, site, project_title, False, templates=())
    if type(existing_project.get("project_id")) is not int:
        raise Blocked("Missing or drifted legacy project prerequisites")
    pid = existing_project["project_id"]
    base = f"/project/{pid}"
    path = base + "/templates?sort=name&order=asc"
    items = api.request("GET", path)
    views = api.request("GET", base + "/views")
    repositories = api.request("GET", base + "/repositories?sort=name&order=asc")
    inventory = api.request("GET", base + "/inventory?sort=name&order=asc")
    environments = api.request("GET", base + "/environment?sort=name&order=asc")
    if not all(isinstance(t, list) for t in
               (items, views, repositories, inventory, environments)):
        raise Blocked("Unexpected Semaphore resource data for legacy migration")
    repo = one_named(repositories, "DigitalHouses code")
    inv = one_named(inventory, "Runner (local)")
    env = one_named(environments, "Empty")
    view_ids = {v.get("title"): v.get("id") for v in views}
    if not (repo and inv and env and all(v in view_ids for v in VIEWS)):
        raise Blocked("Legacy resource identities incomplete")
    if len(items) not in (4, 5):
        raise Blocked("Legacy migration requires exactly four existing tasks, optionally recovery 00")
    expected_playbooks = {row[2] for row in LEGACY_MIGRATIONS}
    sync_playbook = BOOTSTRAP_TEMPLATES[0][2]
    all_playbooks = [t.get("playbook") for t in items]
    if (len(set(all_playbooks)) != len(items)
            or not expected_playbooks.issubset(set(all_playbooks))
            or set(all_playbooks) - expected_playbooks - {sync_playbook}):
        raise Blocked("Unknown or duplicated Semaphore playbook: legacy migration blocked")
    recoveries = [t for t in items if t.get("playbook") == sync_playbook]
    if recoveries:
        if len(recoveries) != 1 or recoveries[0].get("name") != BOOTSTRAP_TEMPLATES[0][0]:
            raise Blocked("Recovery task identity drift")
        if recoveries[0].get("description") != MANAGED_DESCRIPTION:
            raise Blocked("Unmanaged recovery task cannot be migrated")
    all_expected = {
        "project_id": pid, "app": "ansible", "arguments": "[]",
        "type": "", "inventory_id": inv["id"],
        "repository_id": repo["id"], "environment_id": env["id"],
        "autorun": False, "allow_override_args_in_task": False,
    }
    plans = []
    for old, new, playbook, oldview, newview in LEGACY_MIGRATIONS:
        matching = [v for v in items if v.get("playbook") == playbook]
        if len(matching) != 1:
            raise Blocked("Missing legacy playbook: " + playbook)
        t = matching[0]
        if t.get("name") not in (old, new):
            raise Blocked("Unexpected renamed legacy template: " + playbook)
        if type(t.get("id")) is not int or t["id"] <= 0:
            raise Blocked("Invalid legacy template ID")
        if any(not template_equal(k, t.get(k), wanted)
               for k, wanted in all_expected.items()):
            raise Blocked("Legacy template configuration drift: " + old)
        expected_view = oldview if t["name"] == old else newview
        if t.get("view_id") != view_ids[expected_view]:
            raise Blocked("Legacy template view drift: " + old)
        description = t.get("description")
        if t["name"] == old and description not in (None, ""):
            raise Blocked("Old template ownership drift: " + old)
        if t["name"] == new and description != MANAGED_DESCRIPTION:
            raise Blocked("Renamed template ownership drift: " + new)
        plans.append((t, old, new, view_ids[newview]))
    for task, old, new, _ in plans:
        if task["name"] == old:
            print(f"PLAN_MIGRATE={old} -> {new} id={task['id']}", flush=True)
        else:
            print(f"MIGRATED_ALREADY={new} id={task['id']}", flush=True)
    if not recoveries:
        print("PLAN_CREATE=" + BOOTSTRAP_TEMPLATES[0][0], flush=True)
    if not apply:
        return {"state": "PLAN_ONLY", "project_id": pid}
    for task, old, new, new_view_id in plans:
        if task["name"] == new:
            continue
        template_id = task["id"]
        detail_path = base + f"/templates/{template_id}"
        detail = api.request("GET", detail_path)
        if (not isinstance(detail, dict) or detail.get("id") != template_id
                or detail.get("name") != old
                or detail.get("playbook") != task["playbook"]
                or any(not template_equal(k, detail.get(k), wanted)
                       for k, wanted in all_expected.items())
                or detail.get("view_id") != task["view_id"]
                or detail.get("description") not in (None, "")):
            raise Blocked("Legacy migration concurrency drift: " + old)
        payload = dict(detail)
        payload.update({"name": new, "view_id": new_view_id,
                        "description": MANAGED_DESCRIPTION})
        api.request("PUT", detail_path, payload)
        after = api.request("GET", detail_path)
        if (not isinstance(after, dict) or after.get("id") != template_id
                or after.get("name") != new
                or after.get("playbook") != task["playbook"]
                or after.get("view_id") != new_view_id
                or after.get("description") != MANAGED_DESCRIPTION):
            raise Blocked("Legacy rename failed verification: " + old)
        print(f"MIGRATED={old} -> {new} id={template_id}", flush=True)
    result = provision(api, site, project_title, True, templates=BOOTSTRAP_TEMPLATES)
    if result["state"] != "APPLIED":
        raise Blocked("Recovery task creation was not confirmed")
    after_all = api.request("GET", path)
    expected_names = {row[1] for row in LEGACY_MIGRATIONS}
    expected_names.add(BOOTSTRAP_TEMPLATES[0][0])
    if (not isinstance(after_all, list)
            or {t.get("name") for t in after_all} != expected_names
            or len(after_all) != len(expected_names)):
        raise Blocked("Post-migration template catalog differs from expected state")
    print("MIGRATION=PASS_EXISTING_IDS_PRESERVED", flush=True)
    return {"state": "APPLIED", "project_id": pid}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site", required=True)
    parser.add_argument("--project-title")
    credentials = parser.add_mutually_exclusive_group(required=True)
    credentials.add_argument("--admin-password-file", type=Path)
    credentials.add_argument("--admin-token-file", type=Path)
    parser.add_argument("--apply", action="store_true")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--sync-catalog", action="store_true")
    modes.add_argument("--migrate-legacy", action="store_true")
    args = parser.parse_args(argv)
    if not re.fullmatch(r"[a-z][a-z0-9_-]{0,30}", args.site):
        raise Blocked("Invalid site ID")
    if os.geteuid() != 0:
        raise Blocked("Run as root inside CT runner, not on PVE")
    if Path("/etc/pve/user.cfg").exists():
        raise Blocked("Never run Seeder on PVE host")
    if os.uname().nodename != f"ansible-{args.site}":
        raise Blocked("Unexpected guest hostname")
    if args.sync_catalog and args.admin_token_file is None:
        raise Blocked("Catalog synchronization requires root-owned revocable API token")
    if args.admin_token_file:
        token = secure_file(args.admin_token_file)
        if not re.fullmatch(r"[A-Za-z0-9_-]{20,256}", token):
            raise Blocked("Invalid Semaphore API token")
        api = API(token=token)
        del token
    else:
        password = secure_file(args.admin_password_file)
        api = API()
        api.login(password)
        del password
        if args.apply:
            create_catalog_api_token_once(api)
    if args.sync_catalog and not args.project_title:
        projects = api.request("GET", "/projects")
        if not isinstance(projects, list) or len(projects) != 1:
            raise Blocked("Cannot infer unique Semaphore project for sync")
        args.project_title = projects[0].get("name")
    if not isinstance(args.project_title, str) or not 1 <= len(args.project_title) <= 100:
        raise Blocked("Invalid project title")
    if args.sync_catalog:
        result = synchronize_catalog(api, args.site, args.project_title, apply=args.apply)
    elif args.migrate_legacy:
        result = migrate_accepted_four_task_catalog(
            api, args.site, args.project_title, apply=args.apply)
    else:
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
