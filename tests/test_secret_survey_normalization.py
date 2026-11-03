"""Native Task 05 Survey: harmless Semaphore API normalization preserves IDs/history.

Six optional fields are owned by Semaphore. Empty inputs mean KEEP.
Secret/text input is never admitted via the old enum-only CLI launcher.
"""
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CODE = ROOT / "bootstrap/semaphore-project.py"
TITLE = "05-Ввести credentials объекта"
PLAYBOOK = "playbooks/obtain-site-credentials.yml"


def module():
    spec = importlib.util.spec_from_file_location("task05_survey_sync", CODE)
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
                if item["title"] == TITLE)


def original_template(mod, survey):
    return {
        "id": 6, "project_id": 1, "app": "ansible",
        "name": TITLE, "description": mod.MANAGED_DESCRIPTION,
        "playbook": PLAYBOOK, "repository_id": 11, "inventory_id": 12,
        "environment_id": 13, "view_id": 14, "arguments": "[]",
        "type": "", "autorun": False, "allow_override_args_in_task": False,
        "survey_vars": survey, "task_id": 9876,
    }


@pytest.mark.parametrize("api_changes", [
    {"values": None},
    {"values": [], "default": None},
    {"values": None, "default_value": "", "target": "env"},
    {"values": [], "value": None},
    {"values": [], "required": None},
])
def test_semantically_equal_secret_survey_is_read_only(api_changes):
    m = module()
    wanted = input_survey()
    observed = [dict(wanted[0], **api_changes), *wanted[1:]]
    desired = original_template(m, wanted)
    current = original_template(m, observed)
    api = FakeTemplateAPI(current.copy())
    path = "/project/1/templates?sort=name&order=asc"

    assert m.equivalent_task05_survey(desired["name"], observed, wanted)
    assert m.allowed_survey_upgrade(desired["name"], observed, wanted)
    m.sync_existing_template(api, path, desired, apply=False)
    m.sync_existing_template(api, path, desired, apply=True)

    assert api.writes == 0
    assert api.record["id"] == 6
    assert api.record["task_id"] == 9876
    assert api.record["survey_vars"] == observed


@pytest.mark.parametrize("changes", [
    {"name": "other_password"},
    {"type": "enum"},
    {"required": True},
    {"title": "Unapproved new label"},
    {"description": "Manual drift"},
    {"values": [{"name": "A", "value": "A"}]},
    {"default": "unsafe-filled-value"},
    {"default_value": "unsafe-filled-value"},
    {"target": "extra"},
    {"id": 123},
])
def test_modified_or_preset_secret_survey_stays_blocked(changes):
    m = module()
    wanted = input_survey()
    observed = [dict(wanted[0], **changes), *wanted[1:]]
    api = FakeTemplateAPI(original_template(m, observed))
    with pytest.raises(m.Blocked, match="Unexpected existing survey"):
        m.sync_existing_template(
            api, "/project/1/templates?sort=name&order=asc",
            original_template(m, wanted), apply=True)
    assert api.writes == 0


def test_missing_secret_survey_gets_created_not_marked_equal():
    m = module()
    wanted = input_survey()
    assert m.allowed_survey_upgrade(TITLE, [], wanted)
    assert not m.equivalent_task05_survey(TITLE, [], wanted)


def test_other_task_surveys_remain_strict():
    m = module()
    wanted = input_survey()
    normalized = [dict(wanted[0], values=None), *wanted[1:]]
    assert not m.equivalent_task05_survey(
        "80-Медиасервер · Развёртывание", normalized, wanted)
    assert not m.allowed_survey_upgrade(
        "80-Медиасервер · Развёртывание", normalized, wanted)
