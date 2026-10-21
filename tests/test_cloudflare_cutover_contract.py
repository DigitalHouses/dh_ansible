"""Guard Cloudflare one-shot cutover and rollback paths without external services."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
HOST = ROOT / "bootstrap/cloudflare-pve-cutover.py"
WORKER = ROOT / "bootstrap/cloudflare-runner-cutover.py"
COMMISSION = ROOT / "bootstrap/cloudflare-commission.py"
REQUEST = ROOT / "playbooks/request-cloudflare-cutover.yml"


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def cfg():
    return {
        "site_id": "demo-home", "account_id": "a" * 32, "zone_id": "b" * 32,
        "zone": "digitalhouses.vip",
        "old_tunnel_id": "11111111-1111-4111-8111-111111111111",
        "old_tunnel_name": "Old Home",
        "new_tunnel_id": "22222222-2222-4222-8222-222222222222",
        "access_app_id": "33333333-3333-4333-8333-333333333333",
        "access_policy_id": "44444444-4444-4444-8444-444444444444",
        "pve_host": "192.0.2.30",
        "hostname": "dh-demo-home-pve.digitalhouses.vip",
        "origin": "https://192.0.2.30:8006",
    }


def test_semaphore_request_has_no_root_or_secret_pass_through():
    play = yaml.safe_load(REQUEST.read_text())[0]
    assert play["hosts"] == "site_runner"
    assert play["become"] is False
    text = REQUEST.read_text()
    assert "dh-cloudflare-switch.path" in text
    assert "SWITCH:{{ cloudflare_cutover_nonce.stdout }}" in text
    assert "cloudflare_cutover_status.stdout == cloudflare_cutover_nonce.stdout ~ '|PASS'" in text
    assert "cloudflare_cutover_nonce.stdout ~ '|FAILED'" in text
    for task in play["pre_tasks"] + play["tasks"]:
        assert "ansible.builtin.shell" not in task
        assert "ansible.builtin.raw" not in task
        cmd = task.get("ansible.builtin.command")
        if cmd:
            assert cmd["argv"][0] in {"systemctl", "hostname", "cat"}


def test_worker_pins_ssh_and_fixed_root_host_script():
    code = WORKER.read_text()
    assert "StrictHostKeyChecking=yes" in code
    assert "UserKnownHostsFile=" in code
    assert "sudo" in code and "/usr/local/sbin/dh-cloudflare-pve-cutover" in code
    assert "192" + ".168.11.30" not in code
    assert "CF_CUTOVER=ALREADY_APPLIED" in code
    assert "SWITCH:(" in code
    assert "os.chmod(name, 0o640)" in code


def test_host_readback_refuses_missing_access_before_cutover(monkeypatch):
    module = load(HOST, "cf_host_guard")
    conf = cfg()
    calls = []

    def fake_api(_token, _method, path):
        calls.append(path)
        if path == "/user/tokens/verify":
            return {"status": "active"}
        if path == "/accounts/" + conf["account_id"] + "/cfd_tunnel/" + conf["old_tunnel_id"]:
            return {"name": conf["old_tunnel_name"], "config_src": "cloudflare", "status": "healthy"}
        if path == "/accounts/" + conf["account_id"] + "/cfd_tunnel/" + conf["new_tunnel_id"]:
            return {"name": conf["site_id"], "config_src": "cloudflare", "status": "inactive"}
        if path == "/zones/" + conf["zone_id"]:
            return {"name": conf["zone"], "account": {"id": conf["account_id"]}}
        if path.endswith("/configurations"):
            return {"config": {"ingress": [
                {"hostname": conf["hostname"], "service": conf["origin"]},
                {"service": "http_status:404"},
            ]}}
        if "/dns_records?" in path:
            return [{"type": "CNAME", "content": conf["new_tunnel_id"] + ".cfargotunnel.com",
                     "proxied": True}]
        if "/access/apps/" in path:
            return {"domain": "*." + conf["zone"], "type": "self_hosted", "policies": []}
        if "/access/policies/" in path:
            return {"decision": "allow", "name": "digitalhouses admin",
                    "include": [{"email": "x@example.test"}, {"email": "y@example.test"}]}
        raise AssertionError(path)

    monkeypatch.setattr(module, "api", fake_api)
    with pytest.raises(module.Block, match="ACCESS_POLICY"):
        module.verify_cloudflare("dummy", conf, require_old_healthy=True)
    assert any("dns_records" in path for path in calls)


def test_host_pve_cutover_rollback_restores_legacy_service(monkeypatch, tmp_path, capsys):
    module = load(HOST, "cf_host_rollback")
    conf = cfg()
    conf["pve_host"] = "192.0.2.30"
    for attr, relative in (
        ("CONFIG", "site.json"), ("API_TOKEN", "api.secret"),
        ("CONNECTOR_TOKEN", "connector.secret"),
        ("DROPIN", "service.d/90-digitalhouses-canonical.conf")
    ):
        monkeypatch.setattr(module, attr, tmp_path / relative)
    module.CONFIG.write_text(json.dumps(conf))
    module.API_TOKEN.write_text("placeholder_value_for_mock_auth")
    module.CONFIG.chmod(0o600)
    module.API_TOKEN.chmod(0o600)
    calls = []

    def fake_systemctl(*args):
        calls.append(args)
        if args[0] == "is-active":
            return "active"
        if args[:2] == ("show", "-P") and args[2] == "User":
            return "root"
        if args[:2] == ("show", "-P") and args[2] == "ExecStart":
            return "run --token hidden"
        return ""

    monkeypatch.setattr(module, "secure_read", lambda path: path.read_text().strip())
    monkeypatch.setattr(module, "systemctl", fake_systemctl)
    monkeypatch.setattr(module.subprocess, "run",
                        lambda *a, **kw: SimpleNamespace(returncode=0,
                                                           stdout="2: iface inet 192.0.2.30/24\n"))
    monkeypatch.setattr(module, "verify_cloudflare", lambda *a, **kw: "inactive")
    monkeypatch.setattr(module, "api", lambda *a, **kw: "mock-connector-token" * 9)
    monkeypatch.setattr(module.shutil, "which", lambda _: "/usr/bin/cloudflared")
    monkeypatch.setattr(module.time, "sleep", lambda _: None)
    with pytest.raises(module.Block, match="HEALTH_TIMEOUT"):
        module.apply()
    assert not module.DROPIN.exists()
    assert not module.CONNECTOR_TOKEN.exists()
    assert calls.count(("restart", "cloudflared.service")) == 2
    assert "CF_ROLLBACK=ATTEMPTED" in capsys.readouterr().out


def test_no_automatic_cutover_on_commission():
    code = COMMISSION.read_text()
    assert "CF_CONNECTOR=UNCHANGED" in code
    assert "CF_API_TOKEN=NOT_REQUESTED" in code
    assert "cloudflared.service" in code
    assert 'checked("pct", "exec"' in code
    assert "dh-cloudflare-switch.path" in code
    service = (ROOT / "bootstrap/dh-cloudflare-switch.service").read_text()
    assert "User=root" in service
    assert "NoNewPrivileges=true" in service


def test_catalog_defines_separate_manual_cutover_task():
    data = json.loads((ROOT / "catalog/semaphore-tasks.json").read_text())
    names = [x["title"] for x in data["tasks"]]
    assert "10-Создание туннеля на PVE" in names
    assert "11-Cloudflare · Переключить туннель" in names
    task = next(x for x in data["tasks"] if x["title"].startswith("11-Cloudflare"))
    assert task["playbook"] == "playbooks/request-cloudflare-cutover.yml"
