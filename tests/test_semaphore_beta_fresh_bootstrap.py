"""Offline contract: fresh Debian 13 site runner uses pinned Semaphore 2.20 Beta."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
GUEST = (ROOT / "bootstrap/semaphore-guest-install.py").read_text(encoding="utf-8")
REPLACE = (ROOT / "bootstrap/replace-site-runner.sh").read_text(encoding="utf-8")
INSTALL = (ROOT / "bootstrap/install.sh").read_text(encoding="utf-8")


class FreshBetaBootstrap(unittest.TestCase):
    def test_beta_version_and_upstream_sha256_pinned(self):
        self.assertIn('SEMAPHORE_VERSION = "2.20.16-beta9"', GUEST)
        self.assertIn('SEMAPHORE_DEB_SHA256 = "7e26db3b09b8e9fe5d8bd5c3956f3640dfc748b290f0b0b282746ba2633bf03a"', GUEST)
        self.assertIn('release = Path(f"/root/dh-semaphore-{SEMAPHORE_VERSION}.deb")', GUEST)
        self.assertIn("sha != SEMAPHORE_DEB_SHA256", GUEST)
        self.assertIn('command("semaphore", "version")', GUEST)

    def test_same_site_fresh_rebuild_supported(self):
        self.assertIn('OLD_SITE="$1"', REPLACE)
        self.assertIn('NEW_SITE="$2"', REPLACE)
        self.assertNotIn('NEW_SITE != "$OLD_SITE"', REPLACE)
        self.assertIn('pct destroy "$CTID"', REPLACE)
        self.assertIn('grep -Fxq "hostname: ansible-$OLD_SITE"', REPLACE)
        self.assertIn("grep -Fxq 'unprivileged: 1'", REPLACE)
        self.assertIn('rootfs: local-lvm:', REPLACE)
        self.assertIn("CTID=1231", REPLACE)
        self.assertIn('EXPECTED_PVE_IP="$4"', REPLACE)

    def test_normal_bootstrap_remains_fresh_only_and_seeds_task_zero(self):
        self.assertIn("CTID=1231", INSTALL)
        self.assertIn("CODE_RELEASE=", INSTALL)
        self.assertNotIn("pct destroy", INSTALL)
        self.assertIn("bootstrap/task-00-setup.sh", INSTALL)
        self.assertIn("BOOTSTRAP_FRESH_DONE=PASS", INSTALL)


if __name__ == "__main__":
    unittest.main()
