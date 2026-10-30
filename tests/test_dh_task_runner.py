"""The generic local CLI must not become an unvetted privileged executor."""
import importlib.util
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/dh-task-runner.py"
spec = importlib.util.spec_from_file_location("dh_task_runner", SCRIPT)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)


@pytest.fixture
def catalog():
    return {
        "schema": 1,
        "tasks": [
            {"title": "80-Медиасервер · Развёртывание",
             "playbook": "playbooks/plan-pve-media-vm.yml", "view": "Deploy",
             "survey_vars": [
                 {"name": "media_vm_action", "type": "enum", "required": True,
                  "values": [{"value": x} for x in ("create", "recreate", "keep")]},
                 {"name": "media_raw_disk_action", "type": "enum", "required": True,
                  "values": [{"value": x} for x in ("import", "erase")]},
             ]},
            {"title": "81-Медиасервер · Acceptance E2E",
             "playbook": "playbooks/media-acceptance.yml", "view": "Accept"},
            {"title": "20-Проверка запуска Ansible",
             "playbook": "playbooks/smoke-semaphore.yml", "view": "Accept"},
        ],
    }


def test_select_any_catalog_task_by_exact_number_or_title(catalog):
    for entry in catalog["tasks"]:
        assert module.resolve_task(catalog, entry["title"][:2]) is entry
        assert module.resolve_task(catalog, entry["title"]) is entry
    with pytest.raises(module.Blocked):
        module.resolve_task(catalog, "99")


def test_required_survey_and_no_survey(catalog):
    e80 = module.resolve_task(catalog, "80")
    e81 = module.resolve_task(catalog, "81")
    assert module.parse_survey(e80["survey_vars"],
                               ["media_raw_disk_action=import", "media_vm_action=keep"]) == [
        "media_raw_disk_action=import", "media_vm_action=keep"]
    assert module.parse_survey(e81.get("survey_vars"), []) == []
    for values in (["media_vm_action=keep"], ["media_vm_action=rm -rf /"],
                   ["media_vm_action=keep", "media_raw_disk_action=erase", "extra=yes"],
                   ["media_vm_action=keep", "media_vm_action=keep",
                    "media_raw_disk_action=import"]):
        with pytest.raises(module.Blocked):
            module.parse_survey(e80["survey_vars"], values)
    with pytest.raises(module.Blocked):
        module.parse_survey(None, ["media_vm_action=erase"])


def test_catalog_cannot_select_arbitrary_plays_or_scripts(catalog):
    bad = json.loads(json.dumps(catalog))
    bad["tasks"][0]["playbook"] = "../../bootstrap/semaphore-host-install.py"
    with pytest.raises(module.Blocked):
        module.resolve_task(bad, "80")
    bad = json.loads(json.dumps(catalog))
    bad["tasks"].append(bad["tasks"][1])
    with pytest.raises(module.Blocked):
        module.resolve_task(bad, "81")


def test_worker_required_by_playbook_not_task_number(tmp_path):
    protected = tmp_path / "protected.yml"
    protected.write_text("dest: /var/lib/semaphore/dh-pve-access-audit.request")
    plain = tmp_path / "plain.yml"
    plain.write_text("debug: msg=ok")
    assert module.task_uses_protected_worker(protected)
    assert not module.task_uses_protected_worker(plain)


def test_generic_launch_is_unprivileged_without_shell_or_user_extra_vars(catalog):
    entry = module.resolve_task(catalog, "81")
    command = module.command_for(Path("/var/lib/semaphore/dh-task-c4cc373"),
                                 Path("/var/lib/ansible/site-data/serzhan-home/hosts.yml"),
                                 entry, [])
    assert command[:5] == ["runuser", "-u", "semaphore", "--", "env"]
    assert "-i" in command
    assert command[-1] == "playbooks/media-acceptance.yml"
    assert "sudo" not in command
    assert "-e" not in command
    assert "-c" not in command
    assert "--" in command
    assert "shell=True" not in SCRIPT.read_text()


def test_checkout_requires_pinned_sha_and_site_runner_scoped_path():
    sha = "a" * 40
    for path in (Path("/tmp/escape"),
                 Path("/var/lib/semaphore/dh-task-bbbbbbb"),
                 Path("/var/lib/semaphore/dh-task-aaaaaaa/../../secret")):
        with pytest.raises(module.Blocked):
            module.verified_checkout(path, sha)


def test_main_requires_root_and_explicit_execute(monkeypatch):
    monkeypatch.setattr(module.os, "geteuid", lambda: 1000)
    with pytest.raises(module.Blocked, match="ROOT_OWNED"):
        module.cli(["--site", "serzhan-home", "--task", "81",
                    "--commit", "a" * 40, "--checkout",
                    "/var/lib/semaphore/dh-task-aaaaaaa", "--execute"])


def test_live_catalog_all_tasks_have_safe_unique_playbooks():
    cat = json.loads((ROOT / "catalog/semaphore-tasks.json").read_text())
    for entry in cat["tasks"]:
        selected = module.resolve_task(cat, entry["title"][:2])
        assert (ROOT / selected["playbook"]).is_file()
        for variable in selected.get("survey_vars", []):
            assert variable["type"] == "enum"


def test_security_denials_are_not_retried_via_any_alternate_executor():
    src = SCRIPT.read_text()
    assert "mcp" not in src.lower()
    assert "SSH_KEY" not in src
    assert "sudo" not in src
    assert "root-owned" in src
    assert "--execute" in src


def test_launcher_must_be_pinned_root_owned_not_mutable_checkout():
    with pytest.raises(module.Blocked, match="LAUNCHER_NOT_ROOT_STAGED"):
        module.check_trusted_launcher()
    assert "scripts/dh-task-runner.py" in str(module.CANONICAL_EXECUTABLE)


def test_bootstrap_stages_root_owned_generic_launcher_and_verifier_pins_it():
    bootstrap = (ROOT / "bootstrap/semaphore-host-install.py").read_text()
    verifier = (ROOT / "bootstrap/dh-pve-media-release-verify.py").read_text()
    assert 'scripts/dh-task-runner.py' in bootstrap
    assert 'scripts/dh-task-runner.py' in verifier
    assert 'stage_pve_access_audit' in bootstrap


def test_launcher_is_not_published_as_a_general_root_command():
    source = SCRIPT.read_text()
    assert "subprocess.run(command_for(" in source
    assert "cwd=checkout" in source
    assert "root-owned" in source
    assert "CHECKOUT_SHA_MISMATCH" in source
    assert "SURVEY_VALUE_NOT_ALLOWED" in source
    assert "WORKER_REQUEST_PENDING" in source
    assert "OTHER_ANSIBLE_RUNNING" in source


def test_destructive_enum_requires_separate_explicit_guardrail():
    src = SCRIPT.read_text()
    assert '"erase"' in src
    assert '"recreate"' in src
    assert "DANGEROUS_SURVEY_VALUES" in src
    assert 'args.execute and dangerous' in src
    assert 'DESTRUCTIVE_SURVEY_APPROVAL_REQUIRED' in src
    assert '--approve-destructive' in src
