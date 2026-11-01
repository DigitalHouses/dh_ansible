"""Offline regression tests for the Semaphore 2.19.16 Task 05 pilot.

No network or real credentials. The fake API stores only dummy test values.
"""
import json
from pathlib import Path
import runpy
import unittest

ROOT = Path(__file__).resolve().parents[1]
app = runpy.run_path(str(ROOT / "scripts/semaphore-obtain-credentials.py"),
                     run_name="test_import")
catalog_api = runpy.run_path(str(ROOT / "bootstrap/semaphore-project.py"),
                             run_name="test_import")


class FakeSemaphore:
    def __init__(self):
        self.keys = {}
        self.groups = {}
        self.calls = []
        self.next_id = 20

    def _id(self):
        self.next_id += 1
        return self.next_id

    def request(self, method, url, body=None):
        self.calls.append((method, url, body))
        if (method, url) == ("GET", "/projects"):
            return [{"name": "serzhan-lab", "id": 7}]
        pre = "/project/7"
        if (method, url) == ("GET", pre + "/keys?sort=name&order=asc"):
            return list(self.keys.values())
        if url.startswith(pre + "/keys/") and method == "GET":
            return self.keys[int(url.rsplit("/", 1)[-1])]
        if (method, url) == ("POST", pre + "/keys"):
            row = {"name": body["name"], "type": body["type"],
                   "project_id": body["project_id"], "id": self._id()}
            self.keys[row["id"]] = row
            return row
        if url.startswith(pre + "/keys/") and method == "PUT":
            key_id = int(url.rsplit("/", 1)[-1])
            self.keys[key_id]["type"] = body["type"]
            self.keys[key_id]["name"] = body["name"]
            return None
        if (method, url) == ("GET", pre + "/environment?sort=name&order=asc"):
            return list(self.groups.values())
        if url.startswith(pre + "/environment/") and method == "GET":
            return self.groups[int(url.rsplit("/", 1)[-1])]
        if (method, url) == ("POST", pre + "/environment"):
            group_id = self._id()
            item = body["secrets"][0]
            row = {"id": group_id, "name": body["name"],
                   "project_id": body["project_id"],
                   "json": body["json"], "env": body["env"],
                   "secrets": [{"name": item["name"], "type": item["type"],
                                "id": self._id()}]}
            self.groups[group_id] = row
            return row
        if url.startswith(pre + "/environment/") and method == "PUT":
            group_id = int(url.rsplit("/", 1)[-1])
            row = self.groups[group_id]
            row["json"] = body["json"]
            row["env"] = body["env"]
            for item in body["secrets"]:
                existing = [x for x in row["secrets"] if x["name"] == item["name"]]
                if not existing:
                    row["secrets"].append({"id": self._id(), "name": item["name"],
                                           "type": item["type"]})
            return None
        raise AssertionError("Unexpected API request " + method + " " + url)


class NativeCredentialPilotTest(unittest.TestCase):
    def setUp(self):
        self.api = FakeSemaphore()

    def test_first_enrollment_uses_only_native_api(self):
        result = app["enroll"](self.api, "serzhan-lab",
                               "DUMMY-K1", "DUMMY-V1")
        self.assertEqual(result, {"key_store": "CREATED", "variable_group": "CREATED"})
        self.assertEqual(len(self.api.keys), 1)
        self.assertEqual(len(self.api.groups), 1)
        group = next(iter(self.api.groups.values()))
        self.assertEqual(group["secrets"][0]["type"], "env")
        self.assertNotIn("DUMMY-K1", json.dumps(self.api.keys))
        self.assertNotIn("DUMMY-V1", json.dumps(self.api.groups))

    def test_empty_input_never_touches_api(self):
        self.assertEqual(app["enroll"](self.api, "serzhan-lab", "", ""),
                         {"key_store": "KEEP", "variable_group": "KEEP"})
        self.assertEqual(self.api.calls, [])

    def test_change_only_one_never_updates_the_other(self):
        app["enroll"](self.api, "serzhan-lab", "DUMMY-K1", "DUMMY-V1")
        before = len(self.api.calls)
        result = app["enroll"](self.api, "serzhan-lab", "", "DUMMY-V2")
        self.assertEqual(result, {"key_store": "KEEP", "variable_group": "UPDATED"})
        calls = self.api.calls[before:]
        self.assertFalse(any("/keys" in url for _, url, _ in calls))
        self.assertTrue(any(op == "PUT" and "/environment/" in url
                            and body["secrets"][0]["operation"] == "update"
                            for op, url, body in calls if body))
        self.assertEqual(len(self.api.keys), 1)

    def test_keystore_update_is_explicit_override(self):
        app["enroll"](self.api, "serzhan-lab", "DUMMY-K1", "")
        before = len(self.api.calls)
        result = app["enroll"](self.api, "serzhan-lab", "DUMMY-K2", "")
        self.assertEqual(result["key_store"], "UPDATED")
        calls = self.api.calls[before:]
        edits = [body for method, url, body in calls
                 if method == "PUT" and "/keys/" in url]
        self.assertEqual(len(edits), 1)
        self.assertIs(edits[0]["override_secret"], True)
        self.assertEqual(len(self.api.groups), 0)

    def test_site_mismatch_fails_closed(self):
        with self.assertRaises(app["Blocked"]):
            app["enroll"](self.api, "another-home", "DUMMY", "")
        self.assertEqual(len(self.api.keys), 0)

    def test_pilot_catalog_uses_optional_secret_env_survey(self):
        catalog = json.loads((ROOT / "catalog/semaphore-tasks.json").read_text())
        task = [t for t in catalog["tasks"]
                if t["title"] == "05-Ввести credentials объекта"][0]
        self.assertEqual(task["playbook"], "playbooks/obtain-site-credentials.yml")
        self.assertEqual([x["name"] for x in task["survey_vars"]],
                         ["DH_TEST_KEYSTORE_PASSWORD", "DH_TEST_VARIABLE_SECRET"])
        self.assertTrue(all(x["type"] == "secret" and x["target"] == "env"
                            and x["required"] is False and not x["values"]
                            for x in task["survey_vars"]))

    def test_catalog_migration_accepts_safe_pilot_surveys(self):
        cat = json.loads((ROOT / "catalog/semaphore-tasks.json").read_text())
        survey = next(t["survey_vars"] for t in cat["tasks"]
                      if t["title"] == "05-Ввести credentials объекта")
        self.assertTrue(catalog_api["allowed_survey_upgrade"](
            "05-Ввести credentials объекта", [], survey))
        self.assertTrue(catalog_api["equivalent_task05_survey"](
            "05-Ввести credentials объекта", survey, survey))
        tampered = json.loads(json.dumps(survey))
        tampered[0]["target"] = ""
        self.assertFalse(catalog_api["equivalent_task05_survey"](
            "05-Ввести credentials объекта", tampered, survey))


if __name__ == "__main__":
    unittest.main()
