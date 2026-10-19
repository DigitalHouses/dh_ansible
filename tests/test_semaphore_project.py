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
        arr = self.records.setdefault(p, [])
        if method == "GET":
            return arr.copy()
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
    assert names == ["Проверка запуска Ansible", "Аудит локального runner",
                     "Установить Remote Desktop Commander"]
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
