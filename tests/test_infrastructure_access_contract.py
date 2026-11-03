"""Contract checks: one reusable administrative capability, not a worker per app."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class InfrastructureAccessContractTest(unittest.TestCase):
    def test_shared_linux_admin_preflight(self):
        role = (ROOT / "roles/infrastructure_access/tasks/main.yml").read_text()
        self.assertIn("become: true", role)
        self.assertIn("INFRASTRUCTURE_ACCESS_NOT_COMMISSIONED", role)
        self.assertIn("infrastructure_access_capability", role)
        self.assertNotIn("sudoers.d", role)
        self.assertNotIn("systemctl enable", role)
        self.assertNotIn("apt:", role)

    def test_task40_is_normal_ansible_software_install(self):
        playbook = (ROOT / "playbooks/request-runner-remote-commander.yml").read_text()
        self.assertIn("name: infrastructure_access", playbook)
        self.assertIn("name: runner_remote_commander", playbook)
        self.assertIn("become: true", playbook)
        self.assertNotIn("dh-remote-commander-install.path", playbook)
        self.assertNotIn("dh-remote-commander.request", playbook)
        self.assertNotIn("INSTALL:<uuid>", playbook)

    def test_bootstrap_does_not_grant_software_privileges(self):
        bootstrap = (ROOT / "bootstrap/task-00-setup.sh").read_text()
        self.assertIn("TASK_00_ONLY=PASS", bootstrap)
        self.assertNotIn("stage-remote-commander-watcher", bootstrap)
        self.assertNotIn("dh-remote-commander-install.path", bootstrap)
        self.assertNotIn("NOPASSWD", bootstrap)

    def test_standard_is_not_task_number_specific(self):
        standard = (ROOT / "docs/standards/semaphore-task-design.md").read_text()
        adr = (ROOT / "docs/decisions/0020-infrastructure-privilege-and-software-installation.md").read_text()
        for key in (
            "target", "operations", "identity", "credential_source",
            "approval", "postconditions", "privilege_status",
        ):
            with self.subTest(key=key):
                self.assertIn(key, standard)
                self.assertIn(key, adr)


if __name__ == "__main__":
    unittest.main()
