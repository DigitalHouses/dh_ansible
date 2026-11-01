"""Offline contracts for Ansible-native Semaphore 2.20 credentials onboarding."""
import json
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
OBTAIN = (ROOT / "playbooks/obtain-site-credentials.yml").read_text(encoding="utf-8")
CHECK = (ROOT / "playbooks/check-site-credentials.yml").read_text(encoding="utf-8")
CATALOG = json.loads((ROOT / "catalog/semaphore-tasks.json").read_text(encoding="utf-8"))
REGISTRY = json.loads((ROOT / "catalog/site-credentials.json").read_text(encoding="utf-8"))
SEEDER = (ROOT / "bootstrap/semaphore-project.py").read_text(encoding="utf-8")


class NativeCredentialContract(unittest.TestCase):
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

    def test_approved_five_optional_survey_fields_are_unchanged_for_pinned_seeder(self):
        task05 = next(t for t in CATALOG["tasks"]
                      if t["title"] == "05-Ввести credentials объекта")
        fields = REGISTRY["manual"]
        self.assertEqual(len(fields), 5)
        self.assertEqual([x["name"] for x in task05["survey_vars"]],
                         [f["survey_env"] for f in fields])
        self.assertTrue(all(not x["required"] and x["target"] == "env"
                            and x["values"] == [] for x in task05["survey_vars"]))
        self.assertIn('variable.get("target") != "env"', SEEDER)
        self.assertIn('dh_secret_group_name: Empty', OBTAIN)

    def test_pve_root_only_once_using_native_var_secret(self):
        field = next(x for x in REGISTRY["manual"]
                     if x["id"] == "pve_root_password")
        self.assertEqual(field["store"], "variable_group_secret")
        self.assertEqual(field["semaphore_secret_type"], "var")
        self.assertEqual(field["api_identity"], "root@pam")
        self.assertIn("'type': 'var'", OBTAIN)
        self.assertIn("DH_PVE_ROOT_PASSWORD", CHECK)
        self.assertIn("root@pam", CHECK)
        self.assertIn("/api2/json/access/ticket", CHECK)
        self.assertNotIn("api_token", CHECK.lower())

    def test_partial_keep_and_safe_write_contract(self):
        self.assertIn("rejectattr('value', 'equalto', '')", OBTAIN)
        self.assertIn("items2dict", OBTAIN)
        self.assertIn("if item in dh_supplied", OBTAIN)
        self.assertIn("else 'create'", OBTAIN)
        self.assertIn("SEMAPHORE_SECRET_METADATA_DRIFT", OBTAIN)
        self.assertIn("OBTAIN_CREDENTIALS_BLOCKED", OBTAIN)
        self.assertIn("other_fields: KEEP", OBTAIN)

    def test_task06_has_no_survey_and_is_stage_aware(self):
        task06 = next(t for t in CATALOG["tasks"]
                      if t["title"] == "06-Проверить credentials объекта")
        self.assertEqual(task06["playbook"], "playbooks/check-site-credentials.yml")
        self.assertNotIn("survey_vars", task06)
        self.assertIn("NOT_APPLICABLE_UNTIL_AGENT_COMMISSIONING", CHECK)
        self.assertIn("PVE_TRUST_NOT_COMMISSIONED", CHECK)
        self.assertIn("AUTH_FAILED", CHECK)
        self.assertIn("UNREACHABLE", CHECK)
        self.assertIn("CREDENTIAL_READINESS_BLOCKED", CHECK)
        self.assertIn("validate_certs: true", CHECK)
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
