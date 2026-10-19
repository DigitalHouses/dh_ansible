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
