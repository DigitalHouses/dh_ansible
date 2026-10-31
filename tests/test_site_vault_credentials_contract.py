"""Contract tests: site-local encrypted Vault and one-time secret enrollment."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class SiteVaultCredentialsContract(unittest.TestCase):
    def test_fresh_bootstrap_defaults_to_per_site_vault(self):
        script = (ROOT / "bootstrap/task-00-setup.sh").read_text()
        self.assertIn("credentials_profile: site_vault_v1", script)
        self.assertIn('hostname) == "ansible-$SITE"', script)

    def test_loader_uses_site_ciphertext_and_prevents_cross_site_use(self):
        role = (ROOT / "roles/fleet_credentials/tasks/main.yml").read_text()
        self.assertIn("'/secrets/sites/' ~ site.id ~ '.vault.yml'", role)
        self.assertIn("site-vault-password", role)
        self.assertIn("dh_site_secret_document.site_secrets.site_id == site.id", role)
        self.assertIn("site.credentials_profile | default('fleet_legacy_v1')", role)
        self.assertIn("ansible-vault", role)
        self.assertIn("--vault-id", role)
        self.assertIn("no_log: true", role)
        self.assertIn("SITE_VAULT_WRONG_SITE_OR_FORMAT", role)
        self.assertNotIn("shell: cat", role)

    def test_enrollment_verifies_site_before_replacing_password(self):
        play = (ROOT / "playbooks/request-fleet-master-password.yml").read_text()
        for required in (
            "site_vault_v1",
            "secrets/sites/",
            "fleet_master_password",
            "SITE_VAULT_MISSING",
            "dh_site_vault_candidate.site_secrets.site_id == site.id",
            "SITE_VAULT_WRONG_SITE",
            "dh_candidate_file",
            "SITE_VAULT_READY",
            "mode: '0600'",
            "diff: false",
        ):
            with self.subTest(required=required):
                self.assertIn(required, play)
        self.assertLess(play.index("Verify per-site Vault contains only this site"),
                        play.index("Commit the verified password for all subsequent tasks"))

    def test_runbook_is_recovery_first_and_has_no_credential_values(self):
        doc = (ROOT / "docs/runbooks/site-credentials-onboarding.md").read_text()
        self.assertIn("ansible-vault create --vault-id serzhan-lab@prompt", doc)
        self.assertIn("отдельный", doc)
        self.assertIn("operator-held", doc)
        self.assertIn("вне runner", doc)
        self.assertNotIn("ghp_", doc)


if __name__ == "__main__":
    unittest.main()
