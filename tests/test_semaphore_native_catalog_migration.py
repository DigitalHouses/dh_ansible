"""Task 05 legacy template migration contract, without production writes.

Exercises the actual Semaphore catalog seeder's in-place migration with an
in-memory API stub. No extra dependencies or secrets are required.
"""
import json
from pathlib import Path
import runpy
import unittest

ROOT = Path(__file__).resolve().parents[1]
SEEDER = runpy.run_path(str(ROOT / "bootstrap/semaphore-project.py"),
                        run_name="test_native_catalog_migration")
CATALOG = json.loads((ROOT / "catalog/semaphore-tasks.json").read_text())
TASK05 = next(item for item in CATALOG["tasks"]
              if item["title"] == "05-Ввести credentials объекта")
OLD_NAME, OLD_PLAYBOOK = SEEDER["CATALOG_RENAMES"][TASK05["title"]]
PATH = "/project/7/templates?sort=name&order=asc"


class FakeTemplates:
    def __init__(self):
        self.current = {
            "id": 41,
            "project_id": 7,
            "name": OLD_NAME,
            "playbook": OLD_PLAYBOOK,
            "description": SEEDER["MANAGED_DESCRIPTION"],
            "app": "ansible",
            "repository_id": 12,
            "inventory_id": 13,
            "environment_id": 14,
            "arguments": "[]",
            "type": "",
            "autorun": False,
            "allow_override_args_in_task": False,
            "view_id": 15,
            "survey_vars": [],
            "task_id": 123456,
        }
        self.others = []
        self.writes = []

    def request(self, method, path, body=None):
        if method == "GET":
            if path == PATH:
                return [dict(record) for record in [self.current, *self.others]]
            if path == "/project/7/templates/41":
                return dict(self.current)
        if method == "PUT" and path == "/project/7/templates/41":
            self.writes.append(dict(body))
            self.current.update(body)
            return None
        raise AssertionError("Unexpected API request: " + method + " " + path)


def desired_from(fake):
    desired = dict(fake.current)
    desired["name"] = TASK05["title"]
    desired["playbook"] = TASK05["playbook"]
    desired["survey_vars"] = TASK05["survey_vars"]
    return desired


class NativeCatalogMigrationTest(unittest.TestCase):
    def test_in_place_migration_preserves_id_and_history(self):
        api = FakeTemplates()
        target = desired_from(api)
        sync = SEEDER["sync_existing_template"]
        sync(api, PATH, target, apply=False)
        self.assertEqual(api.writes, [])
        self.assertEqual(api.current["name"], OLD_NAME)
        sync(api, PATH, target, apply=True)
        self.assertEqual(api.current["id"], 41)
        self.assertEqual(api.current["task_id"], 123456)
        self.assertEqual(api.current["name"], TASK05["title"])
        self.assertEqual(api.current["playbook"], TASK05["playbook"])
        self.assertEqual(api.current["survey_vars"], TASK05["survey_vars"])
        self.assertEqual(len(api.writes), 1)
        sync(api, PATH, target, apply=True)
        self.assertEqual(len(api.writes), 1)

    def test_live_five_field_survey_upgrades_to_six_without_losing_history(self):
        api = FakeTemplates()
        desired = desired_from(api)
        api.current["name"] = TASK05["title"]
        api.current["playbook"] = TASK05["playbook"]
        original = [field for field in TASK05["survey_vars"]
                    if field["name"] != "DH_INPUT_MQTT_BROKER_HOST"]
        self.assertEqual(len(original), 5)
        self.assertEqual(len(desired["survey_vars"]), 6)
        api.current["survey_vars"] = original
        sync = SEEDER["sync_existing_template"]
        sync(api, PATH, desired, apply=False)
        self.assertEqual(api.writes, [])
        sync(api, PATH, desired, apply=True)
        self.assertEqual(api.current["survey_vars"], desired["survey_vars"])
        self.assertEqual(api.current["id"], 41)
        self.assertEqual(api.current["task_id"], 123456)
        self.assertEqual(len(api.writes), 1)
        sync(api, PATH, desired, apply=True)
        self.assertEqual(len(api.writes), 1)

    def test_five_field_survey_drift_blocks_mqtt_host_append(self):
        api = FakeTemplates()
        desired = desired_from(api)
        api.current["name"] = TASK05["title"]
        api.current["playbook"] = TASK05["playbook"]
        original = [field.copy() for field in TASK05["survey_vars"]
                    if field["name"] != "DH_INPUT_MQTT_BROKER_HOST"]
        original[2]["type"] = "text"  # password must remain hidden
        api.current["survey_vars"] = original
        with self.assertRaisesRegex(SEEDER["Blocked"], "survey"):
            SEEDER["sync_existing_template"](api, PATH, desired, apply=True)
        self.assertEqual(api.writes, [])

    def test_existing_old_and_new_names_fail_closed(self):
        api = FakeTemplates()
        api.others.append({**api.current, "id": 42,
                           "name": TASK05["title"]})
        with self.assertRaisesRegex(SEEDER["Blocked"], "Conflicting old/new"):
            SEEDER["sync_existing_template"](
                api, PATH, desired_from(api), apply=True)
        self.assertEqual(api.writes, [])

    def test_legacy_playbook_drift_is_rejected(self):
        api = FakeTemplates()
        desired = desired_from(api)
        api.current["playbook"] = "playbooks/smoke-semaphore.yml"
        with self.assertRaises(SEEDER["Blocked"]):
            SEEDER["sync_existing_template"](api, PATH, desired, apply=True)
        self.assertEqual(api.writes, [])

    def test_unmanaged_template_cannot_be_adopted(self):
        api = FakeTemplates()
        desired = desired_from(api)
        api.current["description"] = "Somebody else's task"
        with self.assertRaisesRegex(SEEDER["Blocked"], "not catalog-owned"):
            SEEDER["sync_existing_template"](api, PATH, desired, apply=True)
        self.assertEqual(api.writes, [])

    def test_unexpected_survey_blocks_migration(self):
        api = FakeTemplates()
        desired = desired_from(api)
        api.current["survey_vars"] = [{
            "name": "private_injected_secret",
            "title": "Unknown",
            "description": "",
            "type": "secret",
            "required": False,
            "target": "env",
            "values": [],
        }]
        with self.assertRaisesRegex(SEEDER["Blocked"], "survey"):
            SEEDER["sync_existing_template"](api, PATH, desired, apply=True)
        self.assertEqual(api.writes, [])


if __name__ == "__main__":
    unittest.main()
