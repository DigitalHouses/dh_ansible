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


def test_clean_apply_creates_two_audits_and_one_manual_installer():
    m = module()
    api = FakeAPI()
    result = m.provision(api, "demo-office", "Тестовый дом", True)
    assert result["state"] == "APPLIED"
    names = [c[2]["name"] for c in api.calls if c[0] == "POST" and c[1].endswith("/templates")]
    assert names == ["10-Проверка запуска Ansible", "20-Аудит локального runner",
                     "30-Установить Remote Desktop Commander"]
    assert [c[2]["playbook"] for c in api.calls if c[0] == "POST" and c[1].endswith("/templates")] == [
        "playbooks/smoke-semaphore.yml", "playbooks/audit-runner.yml",
        "playbooks/request-runner-remote-commander.yml"]
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
    assert inventory["inventory"] == "/var/lib/ansimble/site-data/demo-office/hosts.yml"
    assert "repository_id" not in inventory
    assert inventory["ssh_key_id"] == by_name["Runner inventory (local-only)"]["id"]
    templates = next(items for path, items in resources.items()
                     if path.endswith("/templates"))
    assert len(templates) == 3
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
    # Simulate the 3 unnumbered templates present in Semaphore 2.19.
    for existing, (_, aliases, _, _) in zip(original, m.TEMPLATES):
        existing["name"] = aliases[0]
        existing["task_id"] = existing["id"] + 500
    n_post_before = sum(method == "POST" for method, _, _ in api.calls)
    n_put_before = sum(method == "PUT" for method, _, _ in api.calls)
    m.provision(api, "demo-office", "Demo Site", False)
    assert [x["name"] for x in original] == [x[1][0] for x in m.TEMPLATES]
    assert len([x for x in api.calls if x[0] == "PUT"]) == n_put_before
    m.provision(api, "demo-office", "Demo Site", True)
    assert [x["name"] for x in original] == [x[0] for x in m.TEMPLATES]
    assert [x["id"] for x in original] == old_ids
    assert [x["task_id"] for x in original] == [i + 500 for i in old_ids]
    assert sum(method == "POST" for method, _, _ in api.calls) == n_post_before
    assert sum(method == "PUT" for method, _, _ in api.calls) == n_put_before + 3
    # Reapply is a no-op: no duplicate templates or repeated changes.
    m.provision(api, "demo-office", "Demo Site", True)
    assert sum(method == "PUT" for method, _, _ in api.calls) == n_put_before + 3


def test_rename_alias_collision_blocks_without_writing():
    m = module()
    api = FakeAPI()
    m.provision(api, "demo-office", "Demo Site", True)
    path = next(key for key in api.records if key.endswith("/templates"))
    api.records[path].append({**api.records[path][0], "name": m.TEMPLATES[0][1][0],
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
    api.records[path][0]["name"] = m.TEMPLATES[0][1][0]
    api.records[path][0]["autorun"] = True
    before = len([x for x in api.calls if x[0] == "PUT"])
    with pytest.raises(m.Blocked, match="drift"):
        m.provision(api, "demo-office", "Demo Site", True)
    assert len([x for x in api.calls if x[0] == "PUT"]) == before


def test_names_sort_globally_with_ten_step_gaps():
    m = module()
    titles = [name for name, _, _, _ in m.TEMPLATES]
    assert titles == sorted(titles)
    assert [int(name.split("-", 1)[0]) for name in titles] == [10, 20, 30]
    assert all(len(aliases) == 2 for _, aliases, _, _ in m.TEMPLATES)


@pytest.mark.parametrize("alias_index", [0, 1])
def test_old_and_dotted_names_are_migrated_without_duplicates(alias_index):
    m = module()
    api = FakeAPI()
    m.provision(api, "demo-office", "Demo Site", True)
    path = next(p for p in api.records if p.endswith("/templates"))
    resources = api.records[path]
    before_ids = [x["id"] for x in resources]
    for current, (_, aliases, _, _) in zip(resources, m.TEMPLATES):
        current["name"] = aliases[alias_index]
    writes = sum(meth == "POST" for meth, _, _ in api.calls)
    m.provision(api, "demo-office", "Demo Site", False)
    assert [x["name"] for x in resources] == [a[1][alias_index] for a in m.TEMPLATES]
    m.provision(api, "demo-office", "Demo Site", True)
    assert [x["name"] for x in resources] == [a[0] for a in m.TEMPLATES]
    assert [x["id"] for x in resources] == before_ids
    assert sum(meth == "POST" for meth, _, _ in api.calls) == writes
