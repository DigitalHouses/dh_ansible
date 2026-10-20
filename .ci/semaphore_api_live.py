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
