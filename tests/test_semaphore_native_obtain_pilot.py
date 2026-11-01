"""Unit regression for Task 05 native Semaphore Key Store and Variable Groups.

Fake stores dummy secret values internally but never returns them from API reads.
"""
import json
from pathlib import Path
import runpy
import unittest

ROOT = Path(__file__).resolve().parents[1]
app = runpy.run_path(str(ROOT / "scripts/semaphore-obtain-credentials.py"),
                     run_name="test_import")
seeder = runpy.run_path(str(ROOT / "bootstrap/semaphore-project.py"),
                        run_name="test_import")


class FakeSemaphore:
    def __init__(self):
        self.groups = {}
        self.keys = {}
        self.key_passwords = {}
        self.values = {}
        self.calls = []
        self.next_id = 20

    def _id(self):
        self.next_id += 1
        return self.next_id

    def request(self, method, url, body=None):
        self.calls.append((method, url, body))
        if (method, url) == ("GET", "/projects"):
            return [{"name": "serzhan-lab", "id": 7}]
        keypath = "/project/7/keys"
        if (method, url) == ("GET", keypath + "?sort=name&order=asc"):
            return list(self.keys.values())
        if url.startswith(keypath + "/") and method == "GET":
            return self.keys[int(url.rsplit("/", 1)[-1])]
        if (method, url) == ("POST", keypath):
            assert body["type"] == "login_password"
            assert body["login_password"]["login"] == "root"
            key_id = self._id()
            row = {"id": key_id, "name": body["name"],
                   "type": body["type"], "project_id": 7}
            self.keys[key_id] = row
            self.key_passwords[key_id] = body["login_password"]["password"]
            return row
        if url.startswith(keypath + "/") and method == "PUT":
            key_id = int(url.rsplit("/", 1)[-1])
            assert body["override_secret"] is True
            assert body["login_password"]["login"] == "root"
            self.key_passwords[key_id] = body["login_password"]["password"]
            return None
        pre = "/project/7/environment"
        if (method, url) == ("GET", pre + "?sort=name&order=asc"):
            return list(self.groups.values())
        if url.startswith(pre + "/") and method == "GET":
            return self.groups[int(url.rsplit("/", 1)[-1])]
        if (method, url) == ("POST", pre):
            gid = self._id()
            group = {"id": gid, "name": body["name"],
                     "project_id": 7, "json": body["json"], "env": body["env"],
                     "secrets": []}
            self.groups[gid] = group
            self._secrets(gid, body["secrets"])
            return group
        if url.startswith(pre + "/") and method == "PUT":
            gid = int(url.rsplit("/", 1)[-1])
            group = self.groups[gid]
            group["env"] = body["env"]
            self._secrets(gid, body["secrets"])
            return None
        raise AssertionError("Unexpected API call " + method + " " + url)

    def _secrets(self, gid, records):
        for entry in records:
            name = entry["name"]
            group = self.groups[gid]
            old = next((x for x in group["secrets"] if x["name"] == name), None)
            if old is None:
                assert entry["operation"] == "create"
                group["secrets"].append({"name": name, "type": "env",
                                         "id": self._id()})
            else:
                assert entry["operation"] == "update"
                assert entry["id"] == old["id"]
            self.values[name] = entry["secret"]


class NativeCredentialTest(unittest.TestCase):
    def setUp(self):
        self.api = FakeSemaphore()

    def test_first_enrollment_and_hidden_values(self):
        result = app["enroll"](self.api, "serzhan-lab", {
            "github_provision_token": "DUMMY-GITHUB",
            "mqtt_username": "testuser",
            "mqtt_password": "DUMMY-MQTT",
        })
        self.assertEqual(result["github_provision_token"], "CREATED")
        self.assertEqual(result["mqtt_username"], "CREATED")
        self.assertEqual(result["mqtt_password"], "CREATED")
        self.assertEqual(len(self.api.groups), 1)
        group = next(iter(self.api.groups.values()))
        self.assertEqual(json.loads(group["env"])["DH_MQTT_USERNAME"], "testuser")
        self.assertEqual(len(group["secrets"]), 2)
        self.assertNotIn("DUMMY-MQTT", json.dumps(group))
        self.assertNotIn("DUMMY-GITHUB", json.dumps(group))

    def test_zero_inputs_never_call_api(self):
        result = app["enroll"](self.api, "serzhan-lab", {})
        self.assertTrue(all(x == "KEEP" for x in result.values()))
        self.assertEqual(self.api.calls, [])

    def test_mqtt_password_only_preserves_github_and_cf(self):
        app["enroll"](self.api, "serzhan-lab", {
            "mqtt_username": "testuser",
            "mqtt_password": "DUMMY-MQTT-1",
            "cloudflare_api_token": "DUMMY-CF",
            "github_provision_token": "DUMMY-GH",
            "pve_root_password": "DUMMY-ROOT-1",
        })
        before_values = dict(self.api.values)
        before_root = dict(self.api.key_passwords)
        outcome = app["enroll"](self.api, "serzhan-lab", {
            "mqtt_password": "DUMMY-MQTT-2"
        })
        self.assertEqual(outcome["mqtt_password"], "UPDATED")
        self.assertEqual(outcome["cloudflare_api_token"], "KEEP")
        self.assertEqual(outcome["github_provision_token"], "KEEP")
        self.assertEqual(outcome["pve_root_password"], "KEEP")
        self.assertEqual(self.api.key_passwords, before_root)
        self.assertEqual(self.api.values["DH_CLOUDFLARE_API_TOKEN"],
                         before_values["DH_CLOUDFLARE_API_TOKEN"])
        self.assertEqual(self.api.values["DH_GITHUB_PROVISION_TOKEN"],
                         before_values["DH_GITHUB_PROVISION_TOKEN"])
        self.assertEqual(self.api.values["DH_MQTT_PASSWORD"], "DUMMY-MQTT-2")
        group = next(iter(self.api.groups.values()))
        self.assertEqual(json.loads(group["env"])["DH_MQTT_USERNAME"], "testuser")
        self.assertEqual(len(group["secrets"]), 3)

    def test_pve_root_key_is_created_without_variable_group(self):
        result = app["enroll"](self.api, "serzhan-lab", {
            "pve_root_password": "DUMMY-ROOT-1",
        })
        self.assertEqual(result["pve_root_password"], "CREATED")
        self.assertEqual(len(self.api.keys), 1)
        self.assertFalse(self.api.groups)
        key_id = next(iter(self.api.keys))
        self.assertEqual(self.api.keys[key_id]["type"], "login_password")
        self.assertEqual(self.api.key_passwords[key_id], "DUMMY-ROOT-1")
        self.assertNotIn("DUMMY-ROOT-1", json.dumps(self.api.keys))

    def test_pve_root_key_updates_only_itself(self):
        app["enroll"](self.api, "serzhan-lab", {
            "mqtt_password": "DUMMY-MQTT",
            "pve_root_password": "DUMMY-ROOT-1",
        })
        before_vars = dict(self.api.values)
        result = app["enroll"](self.api, "serzhan-lab", {
            "pve_root_password": "DUMMY-ROOT-2",
        })
        self.assertEqual(result["pve_root_password"], "UPDATED")
        self.assertEqual(self.api.values, before_vars)
        self.assertEqual(len(self.api.keys), 1)
        self.assertEqual(next(iter(self.api.key_passwords.values())), "DUMMY-ROOT-2")
        self.assertEqual(len(self.api.groups), 1)

    def test_change_nonsecret_username_preserves_secret_values(self):
        app["enroll"](self.api, "serzhan-lab", {
            "mqtt_password": "DUMMY-MQTT",
            "mqtt_username": "old",
        })
        result = app["enroll"](self.api, "serzhan-lab", {
            "mqtt_username": "new"
        })
        self.assertEqual(result["mqtt_username"], "UPDATED")
        self.assertEqual(self.api.values["DH_MQTT_PASSWORD"], "DUMMY-MQTT")
        group = next(iter(self.api.groups.values()))
        self.assertEqual(json.loads(group["env"])["DH_MQTT_USERNAME"], "new")

    def test_refuse_wrong_site_and_undeclared_field(self):
        with self.assertRaises(app["Blocked"]):
            app["enroll"](self.api, "jodar-home", {"mqtt_password": "DUMMY"})
        with self.assertRaises(app["Blocked"]):
            app["enroll"](self.api, "serzhan-lab", {"unknown_field": "DUMMY"})
        self.assertFalse(self.api.groups)

    def test_survey_matches_five_registry_fields(self):
        catalogue = json.loads((ROOT / "catalog/semaphore-tasks.json").read_text())
        survey = next(t["survey_vars"] for t in catalogue["tasks"]
                      if t["title"] == "05-Ввести credentials объекта")
        approved = app["registry_fields"]()
        self.assertEqual(len(survey), 5)
        self.assertEqual([x["name"] for x in survey],
                         [x["survey_env"] for x in approved])
        self.assertTrue(all(x["target"] == "env" and not x["required"]
                            and x["values"] == [] for x in survey))
        self.assertEqual([x["type"] for x in survey],
                         [x["input"] for x in approved])
        self.assertTrue(seeder["allowed_survey_upgrade"](
            "05-Ввести credentials объекта", [], survey))
        self.assertTrue(seeder["equivalent_task05_survey"](
            "05-Ввести credentials объекта", survey, survey))
        bad = json.loads(json.dumps(survey))
        bad[0]["target"] = ""
        self.assertFalse(seeder["equivalent_task05_survey"](
            "05-Ввести credentials объекта", bad, survey))


if __name__ == "__main__":
    unittest.main()
