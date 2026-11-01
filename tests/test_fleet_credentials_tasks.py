"""Offline contracts for accepted Semaphore-native site credential tasks 05/06."""
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
    assert tasks[1]["playbook"] == "playbooks/obtain-site-credentials.yml"
    assert tasks[2]["playbook"] == "playbooks/check-site-credentials.yml"
    assert len(seeder_module().BOOTSTRAP_TEMPLATES) == 1
    assert all(t["view"] == "01 · Подготовка" for t in tasks[:3])


def test_native_survey_has_no_default_or_choices():
    tasks = json.loads(CATALOG.read_text())["tasks"]
    item05 = next(x for x in tasks if x["title"] == "05-Ввести credentials объекта")
    item06 = next(x for x in tasks if x["title"] == "06-Проверить credentials объекта")
    fields = item05["survey_vars"]
    assert len(fields) == 5
    assert [f["name"] for f in fields] == [
        "DH_INPUT_GITHUB_PROVISION_TOKEN",
        "DH_INPUT_MQTT_USERNAME",
        "DH_INPUT_MQTT_PASSWORD",
        "DH_INPUT_PVE_ROOT_PASSWORD",
        "DH_INPUT_CLOUDFLARE_API_TOKEN",
    ]
    assert [f["type"] for f in fields] == [
        "secret", "text", "secret", "secret", "secret",
    ]
    assert all(f["target"] == "env" and f["required"] is False and f["values"] == []
               and "default" not in f and "default_value" not in f
               for f in fields)
    assert "survey_vars" not in item06


def test_05_uses_native_api_without_custom_credential_worker():
    txt = (ROOT / "playbooks/obtain-site-credentials.yml").read_text()
    assert "ansible.builtin.uri:" in txt
    assert "ansible.builtin.slurp:" in txt
    assert "target=env" in txt
    assert "dh_secret_group_name: Empty" in txt
    assert "when: item in dh_supplied" in txt
    assert "no_log: true" in txt
    assert "validate_certs: true" in txt
    assert "semaphore-obtain-credentials.py" not in txt
    assert "fleet-vault-password" not in txt


def test_06_checks_root_pve_over_trusted_https():
    txt = (ROOT / "playbooks/check-site-credentials.yml").read_text()
    assert "DH_PVE_ROOT_PASSWORD" in txt
    assert "root@pam" in txt
    assert "scripts/pve-https-credential-check.py" in txt
    assert "PVE_TRUST_NOT_COMMISSIONED" in txt
    assert "stdin_add_newline: false" in txt
    assert "no_log: true" in txt
    verifier = (ROOT / "scripts/pve-https-credential-check.py").read_text()
    assert "/api2/json/access/ticket" in verifier
    assert "/api2/json/nodes" in verifier
    assert "ctx.verify_flags &= ~ssl.VERIFY_X509_STRICT" in verifier
    assert "ctx.verify_mode != ssl.CERT_REQUIRED" in verifier
    assert "not ctx.check_hostname" in verifier
    assert "fleet_master_password" not in txt


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


def test_seeder_accepts_only_fixed_native_05_survey(monkeypatch):
    m = seeder_module()
    catalog = json.loads(CATALOG.read_text())
    monkeypatch.setattr(m.urllib.request, "urlopen", fake_urlopen_for(catalog))
    tasks = m.read_public_catalog()
    assert len(tasks) == len(catalog["tasks"])
    assert tasks[1][4][0]["type"] == "secret"
    assert tasks[1][4][0]["target"] == "env"

    bad = json.loads(json.dumps(catalog))
    bad["tasks"][1]["survey_vars"][0]["name"] = "anything_else"
    monkeypatch.setattr(m.urllib.request, "urlopen", fake_urlopen_for(bad))
    with pytest.raises(m.Blocked, match="Invalid Task 05 native credential survey"):
        m.read_public_catalog()

    bad = json.loads(json.dumps(catalog))
    bad["tasks"][1]["survey_vars"][0]["values"] = [{"name": "wrong", "value": "bad"}]
    monkeypatch.setattr(m.urllib.request, "urlopen", fake_urlopen_for(bad))
    with pytest.raises(m.Blocked, match="Invalid Task 05 native credential survey"):
        m.read_public_catalog()

    bad = json.loads(json.dumps(catalog))
    bad["tasks"][1]["survey_vars"][0]["target"] = "extra"
    monkeypatch.setattr(m.urllib.request, "urlopen", fake_urlopen_for(bad))
    with pytest.raises(m.Blocked, match="Invalid Task 05 native credential survey"):
        m.read_public_catalog()
