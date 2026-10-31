"""Secret Survey task 06: repeated catalog sync must preserve template and history.

There is one legitimate password input. Semaphore may normalize an empty
values list, add a row ID, or set an empty default/extra-var target.
"""
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CODE = ROOT / "bootstrap/semaphore-project.py"


def module():
    spec = importlib.util.spec_from_file_location("task06_survey_sync", CODE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakeTemplateAPI:
    def __init__(self, record):
        self.record = record
        self.writes = 0

    def request(self, method, path, payload=None):
        if method == "GET":
            if path.split("?")[0].endswith("/templates"):
                return [self.record.copy()]
            if path.endswith("/templates/6"):
                return self.record.copy()
        if method == "PUT" and path.endswith("/templates/6"):
            assert payload["id"] == 6
            self.record = payload.copy()
            self.writes += 1
            return None
        raise AssertionError((method, path))


def input_survey():
    data = json.loads((ROOT / "catalog/semaphore-tasks.json").read_text())
    return next(item["survey_vars"] for item in data["tasks"]
                if item["title"] == "06-Ввести master password Fleet Vault")


def original_template(mod, survey):
    return {
        "id": 6, "project_id": 1, "app": "ansible",
        "name": "06-Ввести master password Fleet Vault",
        "description": mod.MANAGED_DESCRIPTION,
        "playbook": "playbooks/request-fleet-master-password.yml",
        "repository_id": 11, "inventory_id": 12,
        "environment_id": 13, "view_id": 14,
        "arguments": "[]", "type": "", "autorun": False,
        "allow_override_args_in_task": False,
        "survey_vars": survey,
        "task_id": 9876,
    }


@pytest.mark.parametrize("api_changes", [
    {"values": None},
    {"values": [], "default": None},
    {"values": None, "default_value": "", "target": "extra"},
    {"values": [], "id": 7, "target": "extra_vars"},
    {"values": [], "value": None, "target": "variable"},
])
def test_semantically_equal_secret_survey_is_read_only(api_changes):
    m = module()
    wanted = input_survey()
    observed = [dict(wanted[0], **api_changes)]
    desired = original_template(m, wanted)
    current = original_template(m, observed)
    api = FakeTemplateAPI(current.copy())
    path = "/project/1/templates?sort=name&order=asc"

    assert m.equivalent_secret_survey(desired["name"], observed, wanted)
    assert m.allowed_survey_upgrade(desired["name"], observed, wanted)
    m.sync_existing_template(api, path, desired, apply=False)
    m.sync_existing_template(api, path, desired, apply=True)

    assert api.writes == 0
    assert api.record["id"] == 6
    assert api.record["task_id"] == 9876
    assert api.record["survey_vars"] == observed


@pytest.mark.parametrize("changes", [
    {"name": "other_password"},
    {"type": "string"},
    {"required": False},
    {"title": "Unapproved new label"},
    {"description": "Manual drift"},
    {"values": [{"name": "A", "value": "A"}]},
    {"default": "unsafe-filled-value"},
    {"default_value": "unsafe-filled-value"},
    {"target": "environment"},
    {"target": "env"},
])
def test_modified_or_preset_secret_survey_stays_blocked(changes):
    m = module()
    wanted = input_survey()
    observed = [dict(wanted[0], **changes)]
    api = FakeTemplateAPI(original_template(m, observed))
    with pytest.raises(m.Blocked, match="Unexpected existing survey"):
        m.sync_existing_template(
            api, "/project/1/templates?sort=name&order=asc",
            original_template(m, wanted), apply=True)
    assert api.writes == 0


def test_missing_secret_survey_gets_created_not_marked_equal():
    m = module()
    wanted = input_survey()
    assert m.allowed_survey_upgrade("06-Ввести master password Fleet Vault", [], wanted)
    assert not m.equivalent_secret_survey("06-Ввести master password Fleet Vault", [], wanted)


def test_legacy_media_survey_remains_unchanged_and_strict():
    m = module()
    wanted = input_survey()
    normalized = [dict(wanted[0], values=None)]
    assert not m.equivalent_secret_survey("80-Медиасервер · Развёртывание", normalized, wanted)
