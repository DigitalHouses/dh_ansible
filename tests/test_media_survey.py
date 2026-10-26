"""Task 80 survey: single-choice input, catalog reconciliation, fail-closed create."""
import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
CODE = ROOT / "bootstrap/semaphore-project.py"


def load_module():
    spec = importlib.util.spec_from_file_location("survey_semaphore", CODE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SurveyAPI:
    def __init__(self, row):
        self.row = row
        self.writes = 0

    def request(self, method, path, payload=None):
        if method == "GET":
            if path.split("?")[0].endswith("/templates"):
                return [self.row.copy()]
            if path.endswith("/templates/80"):
                return self.row.copy()
        if method == "PUT" and path.endswith("/templates/80"):
            assert payload["id"] == self.row["id"]
            self.row = payload.copy()
            self.writes += 1
            return {}
        raise AssertionError((method, path))


class MediaSurveyTest(unittest.TestCase):
    def setUp(self):
        self.module = load_module()
        entries = json.loads((ROOT / "catalog/semaphore-tasks.json").read_text())["tasks"]
        media = [row for row in entries if row["title"] == "80-Медиасервер · Развёртывание"]
        self.assertEqual(len(media), 1)
        self.media = media[0]
        self.survey = media[0]["survey_vars"]

    def make_api(self):
        m = self.module
        row = {
            "id": 80, "project_id": 1, "app": "ansible",
            "name": self.media["title"], "description": m.MANAGED_DESCRIPTION,
            "playbook": self.media["playbook"], "repository_id": 11,
            "inventory_id": 12, "environment_id": 13, "view_id": 14,
            "arguments": "[]", "type": "", "autorun": False,
            "allow_override_args_in_task": False,
        }
        return SurveyAPI(row.copy()), row.copy()

    def test_exact_engineer_choices(self):
        self.assertEqual(len(self.survey), 1)
        variable = self.survey[0]
        self.assertEqual(variable["name"], "media_storage_action")
        self.assertEqual(variable["type"], "enum")
        self.assertIs(variable["required"], True)
        self.assertNotIn("default", variable)
        self.assertNotIn("default_value", variable)
        self.assertEqual([o["value"] for o in variable["values"]], ["create", "import"])

    def test_catalog_sync_retains_id_and_is_idempotent(self):
        m = self.module
        api, desired = self.make_api()
        desired["survey_vars"] = self.survey
        route = "/project/1/templates?sort=name&order=asc"
        m.sync_existing_template(api, route, desired, apply=False)
        self.assertEqual(api.writes, 0)
        m.sync_existing_template(api, route, desired, apply=True)
        self.assertEqual(api.row["id"], 80)
        self.assertEqual(api.row["survey_vars"], self.survey)
        self.assertEqual(api.writes, 1)
        m.sync_existing_template(api, route, desired, apply=True)
        self.assertEqual(api.writes, 1)

    def test_manual_survey_drift_blocked(self):
        m = self.module
        api, desired = self.make_api()
        desired["survey_vars"] = self.survey
        api.row["survey_vars"] = [{"name": "custom", "type": "string"}]
        with self.assertRaisesRegex(m.Blocked, "Unexpected existing survey"):
            m.sync_existing_template(api, "/project/1/templates", desired, apply=True)
        self.assertEqual(api.writes, 0)

    def test_create_stops_before_any_vm_action(self):
        play = (ROOT / self.media["playbook"]).read_text()
        assert play.index("Require engineer-selected storage action") < play.index(
            "Load canonical site identity")
        assert "MEDIA_CREATE_NOT_READY" in play
        assert "when: media_storage_action == 'create'" in play
        assert "media_storage_action in ['create', 'import']" in play


if __name__ == "__main__":
    unittest.main()
