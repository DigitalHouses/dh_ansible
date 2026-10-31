"""Guarded generic replacement of one site runner with a fresh CT1231."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class FreshSiteRunnerReplacementTests(unittest.TestCase):
    def test_replacement_is_exactly_scoped(self):
        script = (ROOT / "bootstrap/replace-site-runner.sh").read_text()
        for token in (
            "CTID=1231",
            'grep -Fxq "hostname: ansible-$OLD_SITE"',
            '[[ $NEW_SITE =~',
            'EXPECTED_PVE_IP="$4"',
            "wrong PVE host/IP",
            "unprivileged: 1",
            "rootfs: local-lvm:",
            "PRESERVE_VM2253=PASS",
            "TEMPLATE_READY=PASS",
            "external mount, hook or orphan disk",
            'pct destroy "$CTID"',
            'bash "$STAGING/install.sh"',
            "FRESH_RUNNER=PASS",
        ):
            with self.subTest(token=token):
                self.assertIn(token, script)
        for forbidden in ("qm destroy", "pvesm free", "zpool destroy",
                          "mkfs", "pct destroy 2253", "pct destroy 2232"):
            self.assertNotIn(forbidden, script)

    def test_bootstrap_keeps_site_as_data_and_does_not_overwrite_existing(self):
        bootstrap = (ROOT / "bootstrap/install.sh").read_text()
        self.assertIn("read -r -p", bootstrap)
        self.assertIn("CTID=1231", bootstrap)
        self.assertIn("fresh Bootstrap only", bootstrap)
        self.assertIn('hostname "ansible-$SITE"', bootstrap)
        self.assertIn("TASK_00_ONLY", (ROOT / "bootstrap/task-00-setup.sh").read_text())


if __name__ == "__main__":
    unittest.main()
