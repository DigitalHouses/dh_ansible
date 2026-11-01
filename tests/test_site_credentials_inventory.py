"""Validate the source-backed v1 Task 05 site credential inventory.

The registry has no credentials values and is not yet wired to the UI.
"""
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class SiteCredentialsInventoryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = json.loads(
            (ROOT / "catalog/site-credentials.json").read_text(encoding="utf-8"))
        cls.catalog = json.loads(
            (ROOT / "catalog/semaphore-tasks.json").read_text(encoding="utf-8"))

    def test_names_are_unique_and_no_values_persisted(self):
        r = self.registry
        self.assertEqual(r["schema"], 1)
        all_fields = [x["id"] for section in (
            "manual", "auto_or_external", "deferred") for x in r[section]]
        self.assertEqual(len(all_fields), len(set(all_fields)))
        serialized = json.dumps(r).lower()
        for field in ("secret_value", "private_key_pem", "access_token_value"):
            self.assertNotIn('"' + field + '"', serialized)

    def test_current_form_manual_fields_are_explicitly_reviewed(self):
        manual = self.registry["manual"]
        self.assertEqual(
            {x["id"] for x in manual},
            {"github_provision_token", "mqtt_username", "mqtt_password",
             "pve_api_audit_token", "pve_api_write_token", "cloudflare_api_token"})
        self.assertTrue(all(x["store"] in {
            "variable_group_secret", "variable_group_nonsecret"} for x in manual))
        self.assertTrue(all(x["input"] in {"text", "secret"} for x in manual))
        self.assertTrue(all(x["activate"] and x["consumer"] for x in manual))

    def test_agent_credential_reuse(self):
        manual = {x["id"]: x for x in self.registry["manual"]}
        agents = ("digitalhouses_plex_agent", "digitalhouses_pve_agent")
        for a in agents:
            for f in ("mqtt_username", "mqtt_password"):
                self.assertIn(a, manual[f]["consumer"])
        self.assertFalse(any("plex" in x["id"] or "pve_agent" in x["id"]
                             for x in self.registry["manual"]))
        self.assertIn("plex_local_admin_token",
                      {x["id"] for x in self.registry["auto_or_external"]})

    def test_scope_matches_current_catalog(self):
        titles = [task["title"] for task in self.catalog["tasks"]]
        self.assertEqual(len(titles), 13)
        for n in ("00-", "05-", "06-", "10-", "50-", "70-", "80-", "81-", "90-"):
            self.assertTrue(any(title.startswith(n) for title in titles))
        self.assertFalse(any("PVE Agent" in title for title in titles))
        self.assertFalse(any(title.startswith("07-") for title in titles))
        self.assertTrue(self.registry["rules"]["all_secret_fields_optional"])
        self.assertIn("KEEP", self.registry["rules"]["blank"])


if __name__ == "__main__":
    unittest.main()
