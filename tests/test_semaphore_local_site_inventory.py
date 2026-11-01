"""Regression: Semaphore 2.20 file inventory must use a local site repository.

An absolute file inventory without repository_id is joined to a public-code
checkout by Semaphore 2.20 and never reaches /var/lib/ansible/site-data.
Existing inventory/template IDs must survive the correction.
"""
from pathlib import Path
import runpy
import unittest

ROOT = Path(__file__).resolve().parents[1]
MOD = runpy.run_path(str(ROOT / "bootstrap/semaphore-project.py"))
fix = MOD["reconcile_site_inventory"]
Blocked = MOD["Blocked"]
PATH = "/project/9/inventory"
SITE = "sample-home"
SITE_DIR = "/var/lib/ansible/site-data/" + SITE


class FakeInventoryAPI:
    def __init__(self, record):
        self.record = record
        self.writes = []
        self.seen = []

    def request(self, method, path, body=None):
        self.seen.append((method, path))
        if method == "GET" and path == PATH + "?sort=name&order=asc":
            return [self.record.copy()] if self.record else []
        if method == "GET" and path == PATH + "/31":
            return self.record.copy()
        if method == "PUT" and path == PATH + "/31":
            self.writes.append(("PUT", body.copy()))
            self.record = body.copy()
            return None
        if method == "POST" and path == PATH:
            self.writes.append(("POST", body.copy()))
            self.record = dict(body, id=31)
            return self.record.copy()
        raise AssertionError((method, path))


def legacy():
    return {
        "id": 31, "name": "Runner (local)", "project_id": 9,
        "type": "file", "inventory": SITE_DIR + "/hosts.yml",
        "repository_id": None, "ssh_key_id": 22,
        "become_key_id": None, "template_id": None,
    }


class LocalInventoryRepairTests(unittest.TestCase):
    def test_known_legacy_inventory_is_upgraded_with_same_id(self):
        api = FakeInventoryAPI(legacy())
        result = fix(api, 9, SITE, 22, 27, apply=True)
        self.assertEqual(result["id"], 31)
        self.assertEqual(result["inventory"], "hosts.yml")
        self.assertEqual(result["repository_id"], 27)
        self.assertEqual(result["type"], "file")
        self.assertEqual(result["ssh_key_id"], 22)
        self.assertEqual(len(api.writes), 1)
        self.assertEqual(api.writes[0][0], "PUT")
        # The same resource is reused by Task 00 and every downstream template.
        self.assertEqual(fix(api, 9, SITE, 22, 27, apply=True)["id"], 31)
        self.assertEqual(len(api.writes), 1)

    def test_plan_only_does_not_modify_semaphore(self):
        api = FakeInventoryAPI(legacy())
        result = fix(api, 9, SITE, 22, 27, apply=False)
        self.assertEqual(result["id"], 31)
        self.assertEqual(api.writes, [])

    def test_foreign_inventory_is_never_adopted(self):
        for key, value in [
            ("inventory", "../other-site/hosts.yml"),
            ("repository_id", 99),
            ("ssh_key_id", 99),
            ("type", "static"),
            ("name", "Another site"),
            ("become_key_id", 4),
        ]:
            with self.subTest(key=key):
                before = legacy()
                before[key] = value
                api = FakeInventoryAPI(before)
                if key == "name":
                    # Named inventory not found; task may create its own record.
                    # A different named inventory is never mutated or adopted.
                    created = fix(api, 9, SITE, 22, 27, apply=False)
                    self.assertIsNone(created)
                else:
                    with self.assertRaises(Blocked):
                        fix(api, 9, SITE, 22, 27, apply=True)
                self.assertEqual(api.writes, [])

    def test_new_site_uses_file_inventory_with_local_repository_binding(self):
        api = FakeInventoryAPI(None)
        result = fix(api, 9, SITE, 22, 27, apply=True)
        self.assertEqual(result["inventory"], "hosts.yml")
        self.assertEqual(result["repository_id"], 27)
        self.assertEqual(result["project_id"], 9)
        self.assertEqual(len(api.writes), 1)
        self.assertEqual(api.writes[0][0], "POST")

    def test_seeder_wires_local_repository_before_inventory(self):
        source = (ROOT / "bootstrap/semaphore-project.py").read_text()
        self.assertIn('"Site inventory (local)"', source)
        self.assertIn('"git_url": f"{SITE_INVENTORY_BASE}/{site}"', source)
        self.assertIn('reconcile_site_inventory(', source)
        self.assertNotIn('"inventory": f"{SITE_INVENTORY_BASE}/{site}/hosts.yml"', source)


if __name__ == "__main__":
    unittest.main()
