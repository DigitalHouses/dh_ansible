#!/usr/bin/env python3
"""Live Semaphore v2.19 API contract smoke against temporary CI SQLite server."""
from __future__ import annotations
import importlib.util
import os
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "bootstrap/semaphore-project.py"
spec = importlib.util.spec_from_file_location("semaphore_project_ci", SOURCE)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
api = module.API(ca_file=os.environ["CI_SEMAPHORE_CERT"])
api.login(Path(os.environ["CI_SEMAPHORE_PASSWORD_FILE"]).read_text().strip())
result = module.provision(api, "home", "CI · Semaphore Test", True)
assert result["state"] == "APPLIED"
second = module.provision(api, "home", "CI · Semaphore Test", True)
assert second["project_id"] == result["project_id"]
print("SEMAPHORE_2_19_API_REAL_SERVER=PASS")

# Exercise official v2.19.16 template POST/GET/PUT and bearer authentication.
templates = api.request("GET", f"/project/{result['project_id']}/templates")
assert len(templates) == 1 and templates[0]["name"] == "00-Обновить список задач"
token_response = api.request("POST", "/user/tokens")
assert isinstance(token_response, dict)
token = token_response.get("id")
assert isinstance(token, str) and len(token) >= 20
bearer = module.API(ca_file=os.environ["CI_SEMAPHORE_CERT"], token=token)
assert isinstance(bearer.request("GET", "/projects"), list)
module.read_public_catalog = lambda: [
    module.BOOTSTRAP_TEMPLATES[0],
    ("10-Cloudflare · Туннель объекта (план)",
     "10-Cloudflare · Туннель объекта (план)",
     "playbooks/plan-cloudflare-tunnel.yml", module.VIEWS[0]),
    ("20-Проверка запуска Ansible", "20-Проверка запуска Ansible",
     "playbooks/smoke-semaphore.yml", module.VIEWS[3]),
]
synced = module.synchronize_catalog(bearer, "home", "CI · Semaphore Test", apply=True)
assert synced["state"] == "APPLIED"
list_after = bearer.request("GET", f"/project/{result['project_id']}/templates")
assert len(list_after) == 3
id_map = {v["name"]: v["id"] for v in list_after}
assert templates[0]["id"] == id_map["00-Обновить список задач"]
module.synchronize_catalog(bearer, "home", "CI · Semaphore Test", apply=True)
assert id_map == {v["name"]: v["id"] for v in bearer.request(
    "GET", f"/project/{result['project_id']}/templates")}
print("SEMAPHORE_2_19_CATALOG_SELF_SYNC=PASS")

# Simulate restored 4-task Semaphore project and migrate in place through
# the real API. Historical task ids must not change.
legacy = tuple((old, old, playbook, oldview)
               for old, new, playbook, oldview, newview in module.LEGACY_MIGRATIONS)
restored = module.provision(api, "home", "CI · Legacy Restore", True, templates=legacy)
pid = restored["project_id"]
path = f"/project/{pid}/templates"
before = bearer.request("GET", path)
assert len(before) == 4
old_ids = {v["playbook"]: v["id"] for v in before}
# Old seeder did not mark templates with the new catalog ownership text.
for task in before:
    details = bearer.request("GET", path + "/" + str(task["id"]))
    details["description"] = ""
    bearer.request("PUT", path + "/" + str(task["id"]), details)
planned = module.migrate_accepted_four_task_catalog(
    bearer, "home", "CI · Legacy Restore", apply=False)
assert planned["state"] == "PLAN_ONLY"
assert len(bearer.request("GET", path)) == 4
migrated = module.migrate_accepted_four_task_catalog(
    bearer, "home", "CI · Legacy Restore", apply=True)
assert migrated["state"] == "APPLIED"
after = bearer.request("GET", path)
assert len(after) == 5
assert [v["name"] for v in after if v["playbook"] in old_ids] != [
    v["name"] for v in before]
assert all(v["id"] == old_ids[v["playbook"]]
           for v in after if v["playbook"] in old_ids)
assert sum(v["name"] == "00-Обновить список задач" for v in after) == 1
module.migrate_accepted_four_task_catalog(
    bearer, "home", "CI · Legacy Restore", apply=True)
assert {v["id"] for v in bearer.request("GET", path)} == {v["id"] for v in after}
print("SEMAPHORE_2_19_RESTORED_MIGRATION=PASS")
