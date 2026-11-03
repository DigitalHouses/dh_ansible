"""Semaphore task 70 is renamed in place, keeping its ID and job history."""
from pathlib import Path
import importlib.util

ROOT = Path(__file__).resolve().parents[1]


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_existing_70_preflight_renames_in_place_without_recreation():
    project = load("bootstrap/semaphore-project.py", "semaphore70")
    fake = load("tests/test_semaphore_project.py", "semaphore_test70")
    api = fake.FakeAPI()
    project.provision(api, "demo-office", "Demo", True)
    path = next(k for k in api.records if k.endswith("/templates"))
    old = dict(api.records[path][0])
    old.update({
        "id": 900,
        "name": "70-Cloudflare · HAOS · mTLS · Предпроверка",
        "playbook": "playbooks/audit-cloudflare-haos-mtls.yml",
        "task_id": 77,
    })
    api.records[path].append(old)
    desired = dict(old)
    desired["name"] = "70-Cloudflare · HAOS · mTLS · Настроить"
    before = len([c for c in api.calls if c[0] in ("POST", "PUT")])
    project.sync_existing_template(api, path, desired, apply=False)
    assert len([c for c in api.calls if c[0] in ("POST", "PUT")]) == before
    project.sync_existing_template(api, path, desired, apply=True)
    assert old["id"] == 900
    assert old["task_id"] == 77
    assert old["name"] == desired["name"]
    assert len([c for c in api.calls if c[0] in ("POST", "PUT")]) == before + 1
    project.sync_existing_template(api, path, desired, apply=True)
    assert len([c for c in api.calls if c[0] in ("POST", "PUT")]) == before + 1
