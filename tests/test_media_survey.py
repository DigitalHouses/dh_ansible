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
        self.assertEqual(len(self.survey), 2)
        expected = {"media_vm_action": ["create", "recreate", "keep"],
                    "media_raw_disk_action": ["import", "erase"]}
        for variable in self.survey:
            self.assertIn(variable["name"], expected)
            self.assertEqual(variable["type"], "enum")
            self.assertIs(variable["required"], True)
            self.assertNotIn("default", variable)
            self.assertNotIn("default_value", variable)
            self.assertEqual([o["value"] for o in variable["values"]],
                             expected[variable["name"]])

    def legacy_survey(self):
        return [
            {"name": name, "title": "Prior deployment select",
             "description": "Migrated only from known task 80 contract",
             "type": "enum", "required": True,
             "values": [{"name": str(value), "value": value} for value in choices]}
            for name, choices in (
                ("media_storage_action", ("create", "import")),
                ("media_vm_policy", ("keep", "recreate")),
                ("media_disk_policy", ("preserve", "erase")))
        ]

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

    def test_existing_legacy_surveys_are_replaced_in_place(self):
        m = self.module
        api, desired = self.make_api()
        desired["survey_vars"] = self.survey
        route = "/project/1/templates?sort=name&order=asc"
        for legacy in (self.legacy_survey()[:1], self.legacy_survey()):
            api.row["survey_vars"] = legacy
            count = api.writes
            m.sync_existing_template(api, route, desired, apply=False)
            self.assertEqual(api.writes, count)
            m.sync_existing_template(api, route, desired, apply=True)
            self.assertEqual(api.row["id"], 80)
            self.assertEqual(api.row["survey_vars"], self.survey)
            self.assertEqual(api.writes, count + 1)
            m.sync_existing_template(api, route, desired, apply=True)
            self.assertEqual(api.writes, count + 1)

    def test_manual_survey_drift_blocked(self):
        m = self.module
        api, desired = self.make_api()
        desired["survey_vars"] = self.survey
        api.row["survey_vars"] = [{"name": "custom", "type": "string"}]
        with self.assertRaisesRegex(m.Blocked, "Unexpected existing survey"):
            m.sync_existing_template(api, "/project/1/templates", desired, apply=True)
        self.assertEqual(api.writes, 0)

    def test_verified_fresh_creation_resets_old_ssh_key_and_waits_for_guest(self):
        worker = (ROOT / "bootstrap/pve-access-audit-request.sh").read_text()
        guest = (ROOT / "playbooks/setup-pve-media-guest.yml").read_text()
        self.assertIn('grep -Fxq "VM_CREATED=YES" "$LOG"', worker)
        self.assertIn('grep -Fxq "VM_RECREATE=REMOVED_VERIFIED_MANAGED" "$LOG"', worker)
        self.assertIn('ssh-keygen -R "$GUEST_IP"', worker)
        self.assertIn("ansible.builtin.wait_for_connection:", guest)
        self.assertIn("timeout: 420", guest)

    def test_engineer_action_reaches_exact_scoped_worker(self):
        play = (ROOT / self.media["playbook"]).read_text()
        worker = (ROOT / "bootstrap/pve-access-audit-request.sh").read_text()
        guest = (ROOT / "playbooks/setup-pve-media-guest.yml").read_text()
        assert "media_vm_action in ['create', 'recreate', 'keep']" in play
        assert "media_raw_disk_action in ['import', 'erase']" in play
        assert "MEDIA_DEPLOY_{{ media_vm_action | upper }}_{{ media_raw_disk_action | upper }}" in play
        assert "MEDIA_DEPLOY_(CREATE|RECREATE|KEEP)_(IMPORT|ERASE)" in worker
        assert "--vm-action" in worker
        assert "--raw-disk-action" in worker
        assert "media_vm_action=$VM_ACTION" in worker
        assert "media_raw_disk_action=$RAW_DISK_ACTION" in worker
        assert "media_vm.yml" in guest
        for legacy in ("media_storage_action", "media_vm_policy", "media_disk_policy"):
            assert legacy not in play
            assert legacy not in worker
            assert legacy not in (ROOT / "roles/media_guest_setup/tasks/main.yml").read_text()


if __name__ == "__main__":
    unittest.main()
