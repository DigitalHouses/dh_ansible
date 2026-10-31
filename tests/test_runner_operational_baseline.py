"""Regression checks for simple local runner administration and Fleet site data."""
import ast
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class RunnerOperationalBaselineTests(unittest.TestCase):
    def test_bootstrap_native_admin_not_worker(self):
        installer = (ROOT / "bootstrap/semaphore-guest-install.py").read_text()
        ast.parse(installer)
        self.assertIn('"sudo"', installer)
        self.assertIn("def configure_local_runner_admin(", installer)
        self.assertIn("semaphore ALL=(ALL:ALL) NOPASSWD: ALL", installer)
        self.assertIn("configure_local_runner_admin(service)", installer)
        self.assertNotIn("NoNewPrivileges=true", installer)
        self.assertNotIn("ProtectSystem=strict", installer)
        self.assertNotIn("ProtectHome=true", installer)

    def test_existing_runner_same_native_admin(self):
        role = (ROOT / "roles/runner_local_admin/tasks/main.yml").read_text()
        self.assertIn("sudoers.d/digitalhouses-semaphore", role)
        self.assertIn("NOPASSWD: ALL", role)
        self.assertIn("NoNewPrivileges=true", role)
        self.assertIn("RUNNER_ADMIN_READY", role)
        self.assertNotIn("dh-remote-commander-install", role)
        self.assertTrue((ROOT / "playbooks/commission-runner-local-admin.yml").is_file())

    def test_site_data_has_no_worker_or_cross_site_paths(self):
        role = (ROOT / "roles/site_inventory_prepare/tasks/main.yml").read_text()
        self.assertIn("site_inventory_required_files", role)
        self.assertIn("fleet/inventories/{{ site.id }}/{{ item }}", role)
        self.assertIn("name: infrastructure_access", role)
        self.assertIn("remote_src: true", role)
        self.assertIn("SITE_DATA_READY", role)
        for forbidden in ("dh-site-data.path", "dh-site-data.request",
                          "dh-site-data.status", "name: pve_root"):
            self.assertNotIn(forbidden, role)

    def test_all_component_consumers_resolve_site_data_before_read(self):
        cases = {
            "playbooks/audit-runner.yml": ["runner.yml"],
            "playbooks/request-netalertx-install.yml": ["netalertx.yml"],
            "playbooks/audit-cloudflare-haos-mtls.yml": ["haos.yml"],
            "playbooks/plan-pve-media-vm.yml": ["pve.yml", "media_vm.yml"],
            "playbooks/media-acceptance.yml": ["pve.yml", "media_vm.yml"],
        }
        for path, names in cases.items():
            with self.subTest(playbook=path):
                text = (ROOT / path).read_text()
                self.assertIn("name: site_inventory_prepare", text)
                for name in names:
                    self.assertIn("          - " + name, text)
                self.assertLess(text.index("name: site_inventory_prepare"),
                                text.index('file: "{{ inventory_dir }}/' + names[0] + '"'))


if __name__ == "__main__":
    unittest.main()
