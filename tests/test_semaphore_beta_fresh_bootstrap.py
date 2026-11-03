"""Offline contract: ordinary fresh Bootstrap, not a guest replacement workflow."""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
GUEST = (ROOT / "bootstrap/semaphore-guest-install.py").read_text(encoding="utf-8")
INSTALL = (ROOT / "bootstrap/install.sh").read_text(encoding="utf-8")
TASK_00 = (ROOT / "bootstrap/task-00-setup.sh").read_text(encoding="utf-8")


class FreshBetaBootstrap(unittest.TestCase):
    def test_beta_version_and_upstream_sha256_pinned(self):
        self.assertIn('SEMAPHORE_VERSION = "2.20.16-beta9"', GUEST)
        self.assertIn('SEMAPHORE_DEB_SHA256 = "7e26db3b09b8e9fe5d8bd5c3956f3640dfc748b290f0b0b282746ba2633bf03a"', GUEST)
        self.assertIn('release = Path(f"/root/dh-semaphore-{SEMAPHORE_VERSION}.deb")', GUEST)
        self.assertIn("sha != SEMAPHORE_DEB_SHA256", GUEST)
        self.assertIn('command("semaphore", "version")', GUEST)

    def test_standard_bootstrap_never_deletes_or_reuses_guests(self):
        self.assertFalse((ROOT / "bootstrap/replace-site-runner.sh").exists())
        self.assertIn('CTID=1231', INSTALL)
        self.assertIn('if pct config "$CTID" >/dev/null 2>&1 || qm config "$CTID" >/dev/null 2>&1;', INSTALL)
        self.assertIn('BLOCKED: guest ID $CTID already exists; fresh Bootstrap only', INSTALL)
        for command in ("pct destroy", "pct stop", "qm destroy", "qm stop", "--resume"):
            self.assertNotIn(command, INSTALL)
        self.assertIn("--protection 1", INSTALL)
        self.assertIn("--unprivileged 1", INSTALL)

    def test_pinned_four_stage_install_seeds_only_task_zero(self):
        self.assertIn('git clone -q --depth 1 --single-branch --branch main', INSTALL)
        self.assertIn('checkout -q --detach "$CODE_RELEASE"', INSTALL)
        self.assertIn('CODE_GIT_SHA=PASS $ACTUAL', INSTALL)
        self.assertIn('bash /opt/digitalhouses/src/bootstrap/task-00-setup.sh', INSTALL)
        self.assertIn("BOOTSTRAP_FRESH_DONE=PASS", INSTALL)
        self.assertEqual(re.findall(r'=== ([1-4])/4', INSTALL), ["1", "2", "3", "4"])
        self.assertIn('templates[0].get("name") == "00-Обновить список задач"', TASK_00)
        self.assertIn("TASK_00_ONLY=PASS", TASK_00)


if __name__ == "__main__":
    unittest.main()
