"""Offline contract for the two optional, deliberately simple Fleet credential tasks."""
import importlib.util
import io
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "catalog/semaphore-tasks.json"
SEEDER = ROOT / "bootstrap/semaphore-project.py"


def seeder_module():
    spec = importlib.util.spec_from_file_location("dh_cred_seeder", SEEDER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_task_05_06_are_not_bootstrap_tasks():
    tasks = json.loads(CATALOG.read_text())["tasks"]
    assert [item["title"][:2] for item in tasks[:4]] == ["00", "05", "06", "10"]
    assert tasks[1]["playbook"] == "playbooks/request-github-credentials.yml"
    assert tasks[2]["playbook"] == "playbooks/request-fleet-master-password.yml"
    assert len(seeder_module().BOOTSTRAP_TEMPLATES) == 1
    assert all(t["view"] == "01 · Подготовка" for t in tasks[:3])


def test_hidden_password_survey_has_no_default_or_choices():
    tasks = json.loads(CATALOG.read_text())["tasks"]
    item = next(x for x in tasks if x["title"].startswith("06-"))
    assert item["survey_vars"] == [{
        "name": "fleet_master_password",
        "title": "Master password Fleet Vault",
        "description": "Введите один раз. Пароль сохранится в runner и будет использоваться после перезагрузки.",
        "type": "secret",
        "required": True,
        "values": [],
    }]
    assert "survey_vars" not in next(x for x in tasks if x["title"].startswith("05-"))


def test_05_prints_only_device_code_and_never_vault_password():
    txt = (ROOT / "playbooks/request-github-credentials.yml").read_text()
    assert "gh_2.102.0_linux_amd64" in txt
    assert "checksum: sha256:" in txt
    assert "auth login --web" in txt
    assert "github.com/login/device" in txt
    assert "async:" in txt and "ansible.builtin.async_status:" in txt
    assert "repos/DigitalHouses/dh_ansible_fleet_detail" in txt
    assert "GH_CONFIG_DIR" in txt
    assert "fleet-vault-password" not in txt
    assert "become: false" in txt


def test_06_writes_secret_once_without_echoing_or_sudo():
    txt = (ROOT / "playbooks/request-fleet-master-password.yml").read_text()
    assert "type: secret" not in txt  # Defined by the catalog, not printed by tasks
    assert "content: \"{{ fleet_master_password }}\\n\"" in txt
    assert "mode: '0600'" in txt
    assert "ansible-vault view --vault-password-file" in txt
    assert "dh_unlock_check.rc == 0" in txt
    assert "no_log: true" in txt
    assert "become: false" in txt
    assert "master password" in txt


class Response:
    status = 200

    def __init__(self, body):
        self._body = io.BytesIO(body)

    def read(self, size=-1):
        return self._body.read(size)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None


def fake_urlopen_for(catalog):
    sha = "a" * 40

    def open_url(request, timeout=20):
        url = request.full_url
        if url.endswith("/commits/main"):
            return Response(json.dumps({"sha": sha}).encode())
        if url.endswith("/catalog/semaphore-tasks.json"):
            return Response(json.dumps(catalog).encode())
        path = url.split("/" + sha + "/", 1)[-1]
        assert path.startswith("playbooks/")
        return Response((ROOT / path).read_bytes())

    return open_url


def test_seeder_accepts_only_fixed_secret_survey(monkeypatch):
    m = seeder_module()
    catalog = json.loads(CATALOG.read_text())
    monkeypatch.setattr(m.urllib.request, "urlopen", fake_urlopen_for(catalog))
    tasks = m.read_public_catalog()
    assert len(tasks) == len(catalog["tasks"])
    assert tasks[2][4][0]["type"] == "secret"

    bad = json.loads(json.dumps(catalog))
    bad["tasks"][2]["survey_vars"][0]["name"] = "anything_else"
    monkeypatch.setattr(m.urllib.request, "urlopen", fake_urlopen_for(bad))
    with pytest.raises(m.Blocked, match="secret survey"):
        m.read_public_catalog()

    bad = json.loads(json.dumps(catalog))
    bad["tasks"][2]["survey_vars"][0]["values"] = [{"name": "wrong", "value": "bad"}]
    monkeypatch.setattr(m.urllib.request, "urlopen", fake_urlopen_for(bad))
    with pytest.raises(m.Blocked, match="secret survey"):
        m.read_public_catalog()
