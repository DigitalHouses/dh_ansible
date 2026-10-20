"""Semaphore v2.19.16 API bootstrap tests; no network or production writes."""
from pathlib import Path
import importlib.util
import pytest

BASE = Path(__file__).resolve().parents[1] / "bootstrap/semaphore-project.py"


def module():
    spec = importlib.util.spec_from_file_location("dh_semaphore_project", BASE)
    item = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(item)
    return item


class FakeAPI:
    def __init__(self):
        self.records = {"/projects": []}
        self.nextid = 10
        self.calls = []
    def request(self, method, path, body=None):
        p = path.split("?")[0]
        self.calls.append((method, p, body))
        if "/templates/" in p:
            base, sid = p.rsplit("/", 1)
            resource = self.records.setdefault(base, [])
            found = [r for r in resource if r["id"] == int(sid)]
            assert len(found) == 1
            if method == "GET":
                return found[0].copy()
            if method == "PUT":
                assert body["id"] == found[0]["id"]
                found[0].update(body)
                return None
            raise AssertionError("No other per-ID mutations allowed")
        arr = self.records.setdefault(p, [])
        if method == "GET":
            return [r.copy() for r in arr]
        if method == "POST":
            self.nextid += 1
            new = {**body, "id": self.nextid}
            arr.append(new)
            return new
        raise AssertionError("No other methods allowed")


def test_clean_bootstrap_apply_creates_only_self_sync():
    m = module()
    api = FakeAPI()
    result = m.provision(api, "demo-office", "Тестовый дом", True)
    assert result["state"] == "APPLIED"
    names = [c[2]["name"] for c in api.calls if c[0] == "POST" and c[1].endswith("/templates")]
    assert names == ["00-Обновить список задач"]
    assert [c[2]["playbook"] for c in api.calls if c[0] == "POST" and c[1].endswith("/templates")] == [
        "playbooks/request-semaphore-catalog-sync.yml"]
    count = len([c for c in api.calls if c[0] == "POST"])
    again = m.provision(api, "demo-office", "Тестовый дом", True)
    assert again["state"] == "APPLIED"
    assert len([c for c in api.calls if c[0] == "POST"]) == count


def test_drift_is_blocked_without_overwriting():
    m = module()
    api = FakeAPI()
    m.provision(api, "demo-office", "Тестовый дом", True)
    repo = next(v for k, v in api.records.items() if k.endswith("/repositories"))
    repo[0]["git_url"] = "git@github.com:other-org/repo.git"
    with pytest.raises(m.Blocked, match="drift"):
        m.provision(api, "demo-office", "Тестовый дом", True)
    assert not [c for c in api.calls if c[0] in ("PUT", "DELETE")]


def test_new_project_plan_never_posts():
    m = module()
    api = FakeAPI()
    assert m.provision(api, "demo-office", "New Home", False)["state"] == "PLAN_ONLY"
    assert not [c for c in api.calls if c[0] == "POST"]


def test_seeder_no_db_edits_or_remote_api_or_shell():
    s = BASE.read_text()
    assert "sqlite3" not in s
    assert "subprocess" not in s
    assert "127.0.0.1:3000/api" in s
    assert "secrets/fleet.vault.yml" not in s
    assert "FLEET_GIT_URL" not in s
    assert "git_key_file" not in s


def test_public_code_private_fleet_inventory_boundary():
    m = module()
    api = FakeAPI()
    result = m.provision(api, "demo-office", "Demo Site", True)
    assert result["state"] == "APPLIED"
    resources = {key: value for key, value in api.records.items()}
    keys = next(items for path, items in resources.items() if path.endswith("/keys"))
    by_name = {item["name"]: item for item in keys}
    assert by_name["Public Git (none)"]["type"] == "none"
    assert "ssh" not in by_name["Public Git (none)"]
    assert "Fleet Detail Git (RO)" not in by_name
    assert by_name["Runner inventory (local-only)"]["type"] == "login_password"
    repos = next(items for path, items in resources.items() if path.endswith("/repositories"))
    assert len(repos) == 1
    code = repos[0]
    assert code["git_url"] == "https://github.com/DigitalHouses/dh_ansible.git"
    assert code["ssh_key_id"] == by_name["Public Git (none)"]["id"]
    inventory = next(items for path, items in resources.items() if path.endswith("/inventory"))[0]
    assert inventory["type"] == "file"
    assert inventory["inventory"] == "/var/lib/ansible/site-data/demo-office/hosts.yml"
    assert "repository_id" not in inventory
    assert inventory["ssh_key_id"] == by_name["Runner inventory (local-only)"]["id"]
    templates = next(items for path, items in resources.items()
                     if path.endswith("/templates"))
    assert len(templates) == 1
    assert all(item["repository_id"] == code["id"] for item in templates)
    assert all(item["inventory_id"] == inventory["id"] for item in templates)


def test_site_inventory_drift_blocks_without_mutation():
    m = module()
    api = FakeAPI()
    m.provision(api, "demo-office", "Demo Site", True)
    inventory = next(items for path, items in api.records.items() if path.endswith("/inventory"))
    inventory[0]["inventory"] = "/tmp/untrusted.yml"
    posts = len([c for c in api.calls if c[0] == "POST"])
    with pytest.raises(m.Blocked, match="drift"):
        m.provision(api, "demo-office", "Demo Site", True)
    assert len([c for c in api.calls if c[0] == "POST"]) == posts
    assert not [c for c in api.calls if c[0] in ("PUT", "DELETE")]


def test_numbered_templates_retain_existing_ids_and_task_history():
    m = module()
    api = FakeAPI()
    m.provision(api, "demo-office", "Demo Site", True)
    path = next(key for key in api.records if key.endswith("/templates"))
    original = list(api.records[path])
    old_ids = [x["id"] for x in original]
    # Simulate originally seeded unnumbered templates (including audit 40).
    for existing, (_, old_name, _, _) in zip(original, m.BOOTSTRAP_TEMPLATES):
        existing["name"] = old_name
        existing["task_id"] = existing["id"] + 500
    n_post_before = sum(method == "POST" for method, _, _ in api.calls)
    n_put_before = sum(method == "PUT" for method, _, _ in api.calls)
    m.provision(api, "demo-office", "Demo Site", False)
    assert [x["name"] for x in original] == [x[1] for x in m.BOOTSTRAP_TEMPLATES]
    assert len([x for x in api.calls if x[0] == "PUT"]) == n_put_before
    m.provision(api, "demo-office", "Demo Site", True)
    assert [x["name"] for x in original] == [x[0] for x in m.BOOTSTRAP_TEMPLATES]
    assert [x["id"] for x in original] == old_ids
    assert [x["task_id"] for x in original] == [i + 500 for i in old_ids]
    assert sum(method == "POST" for method, _, _ in api.calls) == n_post_before
    assert sum(method == "PUT" for method, _, _ in api.calls) == n_put_before + 1
    # Reapply is a no-op: no duplicate templates or repeated changes.
    m.provision(api, "demo-office", "Demo Site", True)
    assert sum(method == "PUT" for method, _, _ in api.calls) == n_put_before + 1


def test_rename_alias_collision_blocks_without_writing():
    m = module()
    api = FakeAPI()
    m.provision(api, "demo-office", "Demo Site", True)
    path = next(key for key in api.records if key.endswith("/templates"))
    api.records[path].append({**api.records[path][0], "name": m.BOOTSTRAP_TEMPLATES[0][1],
                              "id": 999})
    before = len([x for x in api.calls if x[0] in ("POST", "PUT")])
    with pytest.raises(m.Blocked, match="Conflicting old/new"):
        m.provision(api, "demo-office", "Demo Site", True)
    assert len([x for x in api.calls if x[0] in ("POST", "PUT")]) == before


def test_rename_blocks_config_drift_instead_of_overwriting():
    m = module()
    api = FakeAPI()
    m.provision(api, "demo-office", "Demo Site", True)
    path = next(key for key in api.records if key.endswith("/templates"))
    api.records[path][0]["name"] = m.BOOTSTRAP_TEMPLATES[0][1]
    api.records[path][0]["autorun"] = True
    before = len([x for x in api.calls if x[0] == "PUT"])
    with pytest.raises(m.Blocked, match="drift"):
        m.provision(api, "demo-office", "Demo Site", True)
    assert len([x for x in api.calls if x[0] == "PUT"]) == before


def test_names_sort_globally_with_ten_step_gaps():
    m = module()
    titles = [name for name, _, _, _ in m.BOOTSTRAP_TEMPLATES]
    assert titles == sorted(titles)
    assert [int(name.split("-", 1)[0]) for name in titles] == [0]
    assert all(isinstance(previous, str) for _, previous, _, _ in m.BOOTSTRAP_TEMPLATES)



def test_unnumbered_names_migrate_without_duplicates():
    m = module()
    api = FakeAPI()
    m.provision(api, "demo-office", "Demo Site", True)
    path = next(p for p in api.records if p.endswith("/templates"))
    resources = api.records[path]
    before_ids = [x["id"] for x in resources]
    for current, (_, old_name, _, _) in zip(resources, m.BOOTSTRAP_TEMPLATES):
        current["name"] = old_name
    writes = sum(meth == "POST" for meth, _, _ in api.calls)
    m.provision(api, "demo-office", "Demo Site", False)
    assert [x["name"] for x in resources] == [row[1] for row in m.BOOTSTRAP_TEMPLATES]
    m.provision(api, "demo-office", "Demo Site", True)
    assert [x["name"] for x in resources] == [row[0] for row in m.BOOTSTRAP_TEMPLATES]
    assert [x["id"] for x in resources] == before_ids
    assert sum(meth == "POST" for meth, _, _ in api.calls) == writes


def test_unknown_template_name_is_blocked_without_creating_duplicate():
    m = module()
    api = FakeAPI()
    m.provision(api, "demo-office", "Demo Site", True)
    path = next(p for p in api.records if p.endswith("/templates"))
    api.records[path][0]["name"] = "Нестандартное имя"
    before_writes = len([entry for entry in api.calls if entry[0] in ("POST", "PUT")])
    with pytest.raises(m.Blocked, match="Unexpected Semaphore template name"):
        m.provision(api, "demo-office", "Demo Site", True)
    after_writes = len([entry for entry in api.calls if entry[0] in ("POST", "PUT")])
    assert after_writes == before_writes


def sample_catalog(module):
    return list(module.BOOTSTRAP_TEMPLATES) + [
        ("10-Cloudflare · Туннель объекта (план)",
         "10-Cloudflare · Туннель объекта (план)",
         "playbooks/plan-cloudflare-tunnel.yml", module.VIEWS[0]),
        ("20-Проверка запуска Ansible", "20-Проверка запуска Ansible",
         "playbooks/smoke-semaphore.yml", module.VIEWS[3]),
    ]


def test_00_catalog_creates_tasks_and_repeat_sync_is_idempotent(monkeypatch):
    m = module()
    api = FakeAPI()
    m.provision(api, "demo-office", "Demo", True)
    monkeypatch.setattr(m, "read_public_catalog", lambda: sample_catalog(m))
    m.synchronize_catalog(api, "demo-office", "Demo", apply=True)
    path = next(p for p in api.records if p.endswith("/templates"))
    templates = api.records[path]
    assert [item["name"] for item in templates] == [t[0] for t in sample_catalog(m)]
    original_ids = [item["id"] for item in templates]
    changes = len([call for call in api.calls if call[0] in ("POST", "PUT")])
    m.synchronize_catalog(api, "demo-office", "Demo", apply=True)
    assert [item["id"] for item in templates] == original_ids
    assert len([call for call in api.calls if call[0] in ("POST", "PUT")]) == changes


def test_00_catalog_updates_known_template_in_place(monkeypatch):
    m = module()
    api = FakeAPI()
    m.provision(api, "demo-office", "Demo", True)
    tasks = sample_catalog(m)
    monkeypatch.setattr(m, "read_public_catalog", lambda: tasks)
    m.synchronize_catalog(api, "demo-office", "Demo", apply=True)
    path = next(p for p in api.records if p.endswith("/templates"))
    original = api.records[path][2]
    original_id = original["id"]
    original["task_id"] = 12345
    changed = [*tasks]
    changed[2] = (tasks[2][0], tasks[2][1],
                  "playbooks/audit-runner.yml", tasks[2][3])
    monkeypatch.setattr(m, "read_public_catalog", lambda: changed)
    m.synchronize_catalog(api, "demo-office", "Demo", apply=True)
    assert original["id"] == original_id
    assert original["task_id"] == 12345
    assert original["playbook"] == "playbooks/audit-runner.yml"


def test_00_catalog_rejects_unsafe_existing_collision(monkeypatch):
    m = module()
    api = FakeAPI()
    m.provision(api, "demo-office", "Demo", True)
    monkeypatch.setattr(m, "read_public_catalog", lambda: sample_catalog(m))
    path = next(p for p in api.records if p.endswith("/templates"))
    api.records[path].append({
        "id": 499, "name": "10-Cloudflare · Туннель объекта (план)",
        "playbook": "playbooks/another.yml", "project_id": 10,
        "description": "Not managed by DigitalHouses"})
    before = len([x for x in api.calls if x[0] in ("POST", "PUT")])
    with pytest.raises(m.Blocked, match="drift|not catalog-owned"):
        m.synchronize_catalog(api, "demo-office", "Demo", apply=True)
    after = len([x for x in api.calls if x[0] in ("POST", "PUT")])
    assert before == after


def test_single_initial_task_is_safe_self_sync():
    m = module()
    assert len(m.BOOTSTRAP_TEMPLATES) == 1
    assert m.BOOTSTRAP_TEMPLATES[0][0] == "00-Обновить список задач"
    assert m.BOOTSTRAP_TEMPLATES[0][2] == "playbooks/request-semaphore-catalog-sync.yml"
    sync = (BASE.parent / "semaphore-catalog-sync-request.sh").read_text()
    assert "semaphore:semaphore:600" in sync
    assert "SYNC:" in sync and "SITE=" in sync
    assert "dh-semaphore-catalog-api-token" in sync
    assert "--sync-catalog --apply" in sync
    assert "chown root:semaphore" in sync and "chmod 0640" in sync
    assert "SOURCE_SHA=" in sync
    assert "/etc/pve/user.cfg" not in sync
    play = (BASE.parents[1] / "playbooks/request-semaphore-catalog-sync.yml").read_text()
    assert "become: false" in play
    assert "dh-semaphore-catalog-sync.path" in play
    assert "remote_user:" not in play
    assert "source_commit" in play and "created:" in play


def test_catalog_source_contract_and_recovery_template_restrictions():
    src = BASE.read_text()
    assert "api.github.com/repos/" in src
    assert "raw.githubusercontent.com/" in src
    assert "CATALOG_SOURCE_SHA=" in src
    assert "Catalog cannot change the bootstrap recovery task" in src
    assert "Catalog playbook missing or oversized" in src
    assert "Never rewrite bootstrap recovery task" in src
    assert "Existing Semaphore resource drift" in src
    assert "api.request(\"PUT\"" in src
    assert 'api.request("DELETE"' not in src


def test_self_sync_waits_for_final_worker_status():
    play = (BASE.parents[1] / "playbooks/request-semaphore-catalog-sync.yml").read_text()
    assert "catalog_sync_status.stdout.startswith(catalog_sync_id.stdout ~ '|PASS|')" in play
    assert "catalog_sync_status.stdout == catalog_sync_id.stdout ~ '|FAILED'" in play
    assert "until: catalog_sync_status.stdout.startswith(catalog_sync_id.stdout ~ '|')" not in play
    assert "catalog_sync_status.stdout.split('|') | length == 6" in play


def create_accepted_legacy_four(api, m):
    templates = tuple((old, old, playbook, old_view)
                      for old, _new, playbook, old_view, _view in m.LEGACY_MIGRATIONS)
    m.provision(api, "demo-office", "Demo", True, templates=templates)
    path = next(p for p in api.records if p.endswith("/templates"))
    for template in api.records[path]:
        template.pop("description", None)
    return path


def test_legacy_four_task_migration_preserves_id_and_execution_history(monkeypatch):
    m = module()
    api = FakeAPI()
    path = create_accepted_legacy_four(api, m)
    existing = api.records[path]
    expected_ids = {t["playbook"]: t["id"] for t in existing}
    for t in existing:
        t["task_id"] = 800 + t["id"]
    before_writes = sum(k in ("POST", "PUT") for k, _, _ in api.calls)
    plan = m.migrate_accepted_four_task_catalog(api, "demo-office", "Demo", apply=False)
    assert plan["state"] == "PLAN_ONLY"
    assert sum(k in ("POST", "PUT") for k, _, _ in api.calls) == before_writes
    result = m.migrate_accepted_four_task_catalog(api, "demo-office", "Demo", apply=True)
    assert result["state"] == "APPLIED"
    assert len(existing) == 5
    assert "00-Обновить список задач" in [t["name"] for t in existing]
    for task in existing:
        if task["playbook"] in expected_ids:
            assert task["id"] == expected_ids[task["playbook"]]
            assert task["task_id"] == task["id"] + 800
            assert task["description"] == m.MANAGED_DESCRIPTION
    writes_after = sum(k in ("POST", "PUT") for k, _, _ in api.calls)
    result = m.migrate_accepted_four_task_catalog(api, "demo-office", "Demo", apply=True)
    assert result["state"] == "APPLIED"
    assert sum(k in ("POST", "PUT") for k, _, _ in api.calls) == writes_after
    monkeypatch.setattr(m, "read_public_catalog", lambda: [
        m.BOOTSTRAP_TEMPLATES[0],
        ("10-Cloudflare · Туннель объекта (план)",
         "10-Cloudflare · Туннель объекта (план)",
         "playbooks/plan-cloudflare-tunnel.yml", m.VIEWS[0]),
        *( (new, new, playbook, new_view)
          for _old, new, playbook, _old_view, new_view in m.LEGACY_MIGRATIONS),
    ])
    m.synchronize_catalog(api, "demo-office", "Demo", apply=True)
    assert len(api.records[path]) == 6


def test_legacy_migration_blocks_collisions_without_any_writes():
    m = module()
    api = FakeAPI()
    path = create_accepted_legacy_four(api, m)
    api.records[path][0]["name"] = "unexpected name"
    before = sum(k in ("POST", "PUT") for k, _, _ in api.calls)
    with pytest.raises(m.Blocked, match="Unexpected renamed"):
        m.migrate_accepted_four_task_catalog(api, "demo-office", "Demo", apply=True)
    assert sum(k in ("POST", "PUT") for k, _, _ in api.calls) == before


def test_migration_descending_order_handles_overlapping_titles():
    m = module()
    assert [n[0][:2] for n in m.LEGACY_MIGRATIONS] == ["40", "30", "20", "10"]
    assert [n[1][:2] for n in m.LEGACY_MIGRATIONS] == ["50", "40", "30", "20"]
