"""Static regression checks for fresh-bootstrap task 40 handoff.

The live guest E2E is independent. These tests never install a systemd unit
or start Remote Desktop Commander on the test host.
"""
from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]


class Task40BootstrapContractTest(unittest.TestCase):
    def test_bootstrap_files_parse(self):
        for name in (
            "bootstrap/task-00-setup.sh",
            "bootstrap/remote-commander-install-request.sh",
            "bootstrap/stage-remote-commander-watcher.sh",
            "bootstrap/upgrade-task40-watcher.sh",
            "bootstrap/semaphore-catalog-sync-request.sh",
        ):
            with self.subTest(name=name):
                subprocess.run(["bash", "-n", str(ROOT / name)], check=True)

    def test_task40_assets_are_pinned_and_present(self):
        script = (ROOT / "bootstrap/stage-remote-commander-watcher.sh").read_text()
        for name in (
            "playbooks/apply-runner-remote-commander.yml",
            "roles/runner_remote_commander/defaults/main.yml",
            "roles/runner_remote_commander/tasks/main.yml",
            "roles/runner_remote_commander/handlers/main.yml",
            "roles/runner_remote_commander/templates/digitalhouses-remote-commander.service.j2",
            "bootstrap/remote-commander-install-request.sh",
            "bootstrap/dh-remote-commander-install.service",
            "bootstrap/dh-remote-commander-install.path",
        ):
            with self.subTest(name=name):
                self.assertTrue((ROOT / name).is_file())
                self.assertIn(name, script)

    def test_only_00_template_and_on_demand_task40_worker(self):
        script = (ROOT / "bootstrap/task-00-setup.sh").read_text()
        self.assertIn('[[ $(git -C "$SRC" rev-parse HEAD) == "$SHA" ]]', script)
        self.assertIn("TASK_00_ONLY=PASS", script)
        self.assertIn("stage-remote-commander-watcher.sh", script)
        stage = (ROOT / "bootstrap/stage-remote-commander-watcher.sh").read_text()
        self.assertIn("TASK_40_WATCHER=PASS REMOTE_AGENT_INSTALLED=NO", stage)
        self.assertIn("systemctl enable --now dh-remote-commander-install.path", stage)
        self.assertNotIn("systemctl enable --now digitalhouses-remote-commander", stage)
        self.assertNotIn("npm install", stage)
        self.assertNotIn("desktop-commander remote", stage)
        self.assertIn("BLOCKED: linked destination:", stage)
        self.assertIn("BLOCKED: altered artifact", stage)
        upgrade = (ROOT / "bootstrap/upgrade-task40-watcher.sh").read_text()
        self.assertIn("TASK_40_EXISTING_RUNNER=PASS", upgrade)
        self.assertIn("pct exec", upgrade)
        self.assertNotIn("pct destroy", upgrade)
        self.assertNotIn("pct stop", upgrade)
        self.assertNotIn("git pull", upgrade)

    def test_request_worker_is_fixed_purpose(self):
        worker = (ROOT / "bootstrap/remote-commander-install-request.sh").read_text()
        self.assertIn("^(INSTALL|PAIR):", worker)
        self.assertIn('ansible-playbook -i "$INVENTORY"', worker)
        self.assertIn('"$BASE/playbooks/apply-runner-remote-commander.yml"', worker)
        self.assertIn("dh-remote-commander-install.request", worker)
        self.assertNotIn('eval ', worker)
        self.assertNotIn('bash -c "$REQUEST_DATA"', worker)


if __name__ == "__main__":
    unittest.main()
