"""Guard in-place Cloudflare task 10 rename while preserving Semaphore execution history."""
from pathlib import Path
import importlib.util
import json
import pytest

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "catalog/semaphore-tasks.json"
SOURCE = ROOT / "bootstrap/semaphore-project.py"
TEST_PROJECT = ROOT / "tests/test_semaphore_project.py"


def module():
    spec = importlib.util.spec_from_file_location("dh_semaphore_rename", SOURCE)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def fake_api():
    spec = importlib.util.spec_from_file_location("dh_semaphore_testing", TEST_PROJECT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m.FakeAPI()


def provision_legacy(m, api):
    m.provision(api, "demo-office", "Demo", True)
    project = next(p for p in api.records if p.endswith("/templates"))
    c = [
        m.BOOTSTRAP_TEMPLATES[0],
        ("10-Cloudflare · Туннель объекта (план)",
         "10-Cloudflare · Туннель объекта (план)",
         "playbooks/plan-cloudflare-tunnel.yml", m.VIEWS[0]),
    ]
    m.read_public_catalog = lambda: c
    m.synchronize_catalog(api, "demo-office", "Demo", apply=True)
    original = next(t for t in api.records[project] if t["name"].startswith("10-"))
    original["task_id"] = 10077
    return project, original


def renamed_catalog(m):
    return [
        m.BOOTSTRAP_TEMPLATES[0],
        ("10-Создание туннеля CF до PVE",
         "10-Создание туннеля CF до PVE",
         "playbooks/request-cloudflare-cutover.yml", m.VIEWS[0]),
    ]


def test_catalog_publishes_exact_accepted_name():
    manifest = json.loads(CATALOG.read_text())
    matches = [t for t in manifest["tasks"] if t["title"].startswith("10-")]
    assert len(matches) == 1
    assert matches[0]["title"] == "10-Создание туннеля CF до PVE"


def test_rename_preserves_identity_and_history_and_is_idempotent():
    m, api = module(), fake_api()
    path, old = provision_legacy(m, api)
    task_id, identity = old["task_id"], old["id"]
    m.read_public_catalog = lambda: renamed_catalog(m)
    writes = len([x for x in api.calls if x[0] in ("PUT", "POST")])
    m.synchronize_catalog(api, "demo-office", "Demo", apply=False)
    assert len([x for x in api.calls if x[0] in ("PUT", "POST")]) == writes
    m.synchronize_catalog(api, "demo-office", "Demo", apply=True)
    assert old["id"] == identity
    assert old["task_id"] == task_id
    assert old["name"] == "10-Создание туннеля CF до PVE"
    assert len([x for x in api.calls if x[0] in ("PUT", "POST")]) == writes + 1
    m.synchronize_catalog(api, "demo-office", "Demo", apply=True)
    assert len([x for x in api.calls if x[0] in ("PUT", "POST")]) == writes + 1


def test_rename_collision_blocks_preflight_without_writes():
    m, api = module(), fake_api()
    path, old = provision_legacy(m, api)
    api.records[path].append({**old, "name": "10-Создание туннеля CF до PVE", "id": 990})
    m.read_public_catalog = lambda: renamed_catalog(m)
    writes = len([x for x in api.calls if x[0] in ("PUT", "POST")])
    with pytest.raises(m.Blocked, match="Conflicting old/new"):
        m.synchronize_catalog(api, "demo-office", "Demo", apply=True)
    assert len([x for x in api.calls if x[0] in ("PUT", "POST")]) == writes


def test_rename_rejects_unrecognized_previous_playbook():
    m, api = module(), fake_api()
    _, old = provision_legacy(m, api)
    old["playbook"] = "playbooks/smoke-semaphore.yml"
    m.read_public_catalog = lambda: renamed_catalog(m)
    writes = len([x for x in api.calls if x[0] in ("PUT", "POST")])
    with pytest.raises(m.Blocked, match="legacy template binding"):
        m.synchronize_catalog(api, "demo-office", "Demo", apply=True)
    assert len([x for x in api.calls if x[0] in ("PUT", "POST")]) == writes


def test_retirement_archives_finished_task_logs_before_deleting(monkeypatch, tmp_path):
    m, api = module(), fake_api()
    monkeypatch.setattr(m, "HISTORY_ARCHIVE", tmp_path / "root-history")
    project_path, old = provision_legacy(m, api)
    legacy = {**old, "name": m.OBSOLETE_CLOUDFLARE[0],
              "playbook": m.OBSOLETE_CLOUDFLARE[1], "id": 700}
    api.records[project_path].append(legacy)
    base = project_path.rsplit("/", 1)[0]

    native = api.request

    def patched(method, path, body=None):
        if path == base + "/tasks?count=200":
            return [{"id": 5, "template_id": 700},
                    {"id": 6, "template_id": 700}]
        if path in (base + "/tasks/5", base + "/tasks/6"):
            ident = int(path.rsplit("/", 1)[1])
            return {"id": ident, "template_id": 700, "status": "success"}
        if path in (base + "/tasks/5/output", base + "/tasks/6/output"):
            return [{"output": "PASS"}]
        if method == "DELETE" and path == base + "/templates/700":
            api.records[project_path] = [
                x for x in api.records[project_path] if x["id"] != 700]
            api.calls.append((method, path, body))
            return None
        return native(method, path, body)

    monkeypatch.setattr(api, "request", patched)
    m.read_public_catalog = lambda: renamed_catalog(m)
    m.synchronize_catalog(api, "demo-office", "Demo", apply=True)
    assert old["name"] == "10-Создание туннеля CF до PVE"
    assert [x for x in api.records[project_path] if x["name"].startswith("11-")] == []
    archive = m.HISTORY_ARCHIVE / ("project-" + str(legacy["project_id"]) + "-cloudflare-11.json")
    assert archive.stat().st_mode & 0o777 == 0o600
    payload = json.loads(archive.read_text())
    assert [x["task"]["id"] for x in payload["tasks"]] == [5, 6]
    assert len([x for x in api.calls if x[0] == "DELETE"]) == 1


def test_retirement_refuses_incomplete_output_and_does_not_delete(monkeypatch, tmp_path):
    m, api = module(), fake_api()
    monkeypatch.setattr(m, "HISTORY_ARCHIVE", tmp_path / "root-history")
    project_path, old = provision_legacy(m, api)
    legacy = {**old, "name": m.OBSOLETE_CLOUDFLARE[0],
              "playbook": m.OBSOLETE_CLOUDFLARE[1], "id": 700}
    api.records[project_path].append(legacy)
    base = project_path.rsplit("/", 1)[0]
    native = api.request

    def patched(method, path, body=None):
        if path == base + "/tasks?count=200":
            return [{"id": 5, "template_id": 700}]
        if path == base + "/tasks/5":
            return {"id": 5, "template_id": 700, "status": "running"}
        if path == base + "/tasks/5/output":
            return [{"output": "NOT FINISHED"}]
        if method == "DELETE":
            raise AssertionError("Do not delete an active template")
        return native(method, path, body)

    monkeypatch.setattr(api, "request", patched)
    with pytest.raises(m.Blocked, match="incomplete or active"):
        m.synchronize_catalog(api, "demo-office", "Demo", apply=True)
    assert legacy in api.records[project_path]
    assert not list((tmp_path / "root-history").glob("*")) if (
        tmp_path / "root-history").exists() else True
