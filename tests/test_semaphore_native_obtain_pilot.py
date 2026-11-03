"""Offline contracts for Ansible-native Semaphore 2.20 credentials onboarding."""
import json
from pathlib import Path
import re
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[1]
OBTAIN = (ROOT / "playbooks/obtain-site-credentials.yml").read_text(encoding="utf-8")
CHECK = (ROOT / "playbooks/check-site-credentials.yml").read_text(encoding="utf-8")
CATALOG = json.loads((ROOT / "catalog/semaphore-tasks.json").read_text(encoding="utf-8"))
REGISTRY = json.loads((ROOT / "catalog/site-credentials.json").read_text(encoding="utf-8"))
SEEDER = (ROOT / "bootstrap/semaphore-project.py").read_text(encoding="utf-8")


class NativeCredentialContract(unittest.TestCase):
    def test_both_playbooks_parse_as_yaml(self):
        for name in ("obtain-site-credentials", "check-site-credentials"):
            document = yaml.safe_load(
                (ROOT / "playbooks" / (name + ".yml")).read_text(encoding="utf-8")
            )
            self.assertIsInstance(document, list)
            self.assertEqual(len(document), 1 if name == "obtain-site-credentials" else 3)
            self.assertEqual(document[0]["hosts"], "site_runner")
            self.assertEqual(document[0]["gather_facts"], False)
            if name == "check-site-credentials":
                self.assertEqual(document[1]["hosts"], "dh_pve_first_enrollment")
                self.assertEqual(document[2]["hosts"], "dh_pve_key_probe")

    def test_no_custom_task05_python(self):
        self.assertFalse((ROOT / "scripts/semaphore-obtain-credentials.py").exists())
        self.assertNotIn("semaphore-obtain-credentials.py", OBTAIN)
        self.assertIn("ansible.builtin.uri:", OBTAIN)
        self.assertIn("ansible.builtin.slurp:", OBTAIN)
        self.assertIn("ca_path: /etc/semaphore/tls.crt", OBTAIN)
        self.assertIn("validate_certs: true", OBTAIN)
        self.assertIn("no_log: true", OBTAIN)
        self.assertNotIn("validate_certs: false", OBTAIN)
        self.assertNotIn("ansible.builtin.shell:", OBTAIN)
        self.assertNotIn("semaphore-obtain-credentials.py", OBTAIN)
        self.assertNotIn("--extra-vars", OBTAIN)

    def test_approved_nine_optional_survey_fields_are_preserved_for_catalog(self):
        task05 = next(t for t in CATALOG["tasks"]
                      if t["title"] == "05-Ввести credentials объекта")
        fields = REGISTRY["manual"]
        self.assertEqual(len(fields), 9)
        self.assertEqual([x["name"] for x in task05["survey_vars"]],
                         [f["survey_env"] for f in fields])
        self.assertTrue(all(not x["required"] and x["target"] == "env"
                            and x["values"] == [] for x in task05["survey_vars"]))
        self.assertIn('variable.get("target") != "env"', SEEDER)
        self.assertIn('dh_secret_group_name: Empty', OBTAIN)
        self.assertIn('DH_MQTT_BROKER_HOST', OBTAIN)
        self.assertIn('DH_INPUT_MQTT_BROKER_HOST', OBTAIN)
        broker = next(f for f in task05['survey_vars'] if f['name'] == 'DH_INPUT_MQTT_BROKER_HOST')
        self.assertEqual(broker['type'], 'text')
        self.assertFalse(broker['required'])
        for name in ('DH_INPUT_PBS_ADMIN_PASSWORD', 'DH_INPUT_B2_KEY_ID',
                     'DH_INPUT_B2_APPLICATION_KEY'):
            row = next(f for f in task05['survey_vars'] if f['name'] == name)
            self.assertEqual(row['type'], 'secret')
            self.assertIn(name, OBTAIN)

    def test_pve_root_only_once_using_native_var_secret(self):
        field = next(x for x in REGISTRY["manual"]
                     if x["id"] == "pve_root_password")
        self.assertEqual(field["store"], "variable_group_secret")
        self.assertEqual(field["semaphore_secret_type"], "var")
        self.assertEqual(field["api_identity"], "root@pam")
        self.assertIn("'type': 'var'", OBTAIN)
        self.assertIn("DH_PVE_ROOT_PASSWORD", CHECK)
        self.assertIn("root@pam", CHECK)
        verifier = (ROOT / "scripts/pve-https-credential-check.py").read_text()
        self.assertIn("/api2/json/access/ticket", verifier)
        self.assertIn("/api2/json/nodes", verifier)
        self.assertIn("ssl.create_default_context", verifier)
        self.assertIn("ctx.verify_flags &= ~ssl.VERIFY_X509_STRICT", verifier)
        self.assertNotIn("DH_PVE_API_TOKEN", CHECK)

    def test_partial_keep_and_safe_write_contract(self):
        self.assertIn("rejectattr('value', 'equalto', '')", OBTAIN)
        self.assertIn("items2dict", OBTAIN)
        play = yaml.safe_load(OBTAIN)[0]
        store = next(t for t in play["tasks"]
                     if t["name"] == "Save provided values in the existing Semaphore Variable Group")
        upsert = next(t for t in store["block"]
                      if t["name"] == "Prepare only submitted secret upserts")
        self.assertEqual(upsert["when"], "item in dh_supplied")
        self.assertIn("else 'create'", OBTAIN)
        self.assertIn("SEMAPHORE_SECRET_METADATA_DRIFT", OBTAIN)
        self.assertIn("OBTAIN_CREDENTIALS_BLOCKED", OBTAIN)
        self.assertIn("other_fields: KEEP", OBTAIN)

    def test_task06_has_no_survey_and_is_stage_aware(self):
        task06 = next(t for t in CATALOG["tasks"]
                      if t["title"] == "06-Проверить credentials объекта")
        self.assertEqual(task06["playbook"], "playbooks/check-site-credentials.yml")
        self.assertNotIn("survey_vars", task06)
        self.assertIn("scripts/mqtt-credential-check.py", CHECK)
        self.assertIn("api.cloudflare.com/client/v4/user/tokens/verify", CHECK)
        self.assertIn("PVE_TRUST_NOT_COMMISSIONED", CHECK)
        self.assertIn("AUTH_FAILED", CHECK)
        self.assertIn("UNREACHABLE", CHECK)
        self.assertIn("CREDENTIAL_READINESS_BLOCKED", CHECK)
        self.assertIn("stdin_add_newline: false", CHECK)
        self.assertNotIn("validate_certs: false", CHECK)
        self.assertIn("no_log: true", CHECK)
        self.assertNotIn("fleet_master_password", CHECK)

    def test_no_infrastructure_side_effects_in_05_and_06(self):
        for source in (OBTAIN, CHECK):
            for forbidden in ("pct destroy", "pct create", "qm destroy",
                              "apt-get install", "pip install", "git clone"):
                self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
