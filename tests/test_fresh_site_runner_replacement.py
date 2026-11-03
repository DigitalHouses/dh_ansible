"""Guarded generic replacement of one site runner with a fresh CT1231."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class FreshSiteRunnerReplacementTests(unittest.TestCase):
    def test_destructive_runner_replacement_is_retired_and_not_reintroduced(self):
        # The owner-approved Tiny Bootstrap cannot destroy/replace an occupied
        # CT. Legacy site replacement was intentionally retired after the
        # incident; its safety assertions no longer describe an executable.
        self.assertFalse((ROOT / "bootstrap/replace-site-runner.sh").exists())
        bootstrap = (ROOT / "bootstrap/install.sh").read_text()
        self.assertIn('pct config "$CTID"', bootstrap)
        self.assertIn('qm config "$CTID"', bootstrap)
        self.assertIn("fresh Bootstrap only", bootstrap)
        for forbidden in ("pct destroy", "qm destroy", "pvesm free",
                          "replace-site-runner.sh", "--resume-semaphore"):
            self.assertNotIn(forbidden, bootstrap)

    def test_bootstrap_keeps_site_as_data_and_does_not_overwrite_existing(self):
        bootstrap = (ROOT / "bootstrap/install.sh").read_text()
        self.assertIn("read -r -p", bootstrap)
        self.assertIn("CTID=1231", bootstrap)
        self.assertIn("fresh Bootstrap only", bootstrap)
        self.assertIn('hostname "ansible-$SITE"', bootstrap)
        self.assertIn("TASK_00_ONLY", (ROOT / "bootstrap/task-00-setup.sh").read_text())


if __name__ == "__main__":
    unittest.main()
