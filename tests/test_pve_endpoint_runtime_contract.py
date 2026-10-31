"""PVE IPv4 comes from the actual host at Bootstrap, not Fleet or VM numbering."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class PveRuntimeContractTests(unittest.TestCase):
    def test_bootstrap_passes_live_vmbr0_ipv4(self):
        host = (ROOT / "bootstrap/install.sh").read_text()
        guest = (ROOT / "bootstrap/task-00-setup.sh").read_text()
        self.assertIn("ip -4 -o addr show dev vmbr0", host)
        self.assertIn('"\u0024{HOST_CIDR%/*}"', host)
        self.assertIn('PVE_IPV4="\u0024{3:?actual PVE IPv4 from host vmbr0 required}"', guest)
        self.assertIn("pve-runtime.yml", guest)
        self.assertIn("source: bootstrap_vmbr0", guest)
        self.assertIn("root:semaphore", guest)
        self.assertNotRegex(host, r'PVE_IPV4=[0-9]+[.][0-9]+[.][0-9]+[.][0-9]+')

    def test_site_inventory_never_trusts_fleet_ip_over_local_observation(self):
        role = (ROOT / "roles/site_inventory_prepare/tasks/main.yml").read_text()
        self.assertIn("PVE_RUNTIME_ENDPOINT_MISSING", role)
        self.assertIn("pve-runtime.yml", role)
        self.assertIn("site_pve_source.content", role)
        self.assertIn("'host': pve_observed.ipv4", role)
        self.assertIn("'tls': site_pve_fleet.pve.tls", role)
        self.assertIn("host_root_vmbr0", role)
        self.assertIn("to_nice_yaml", role)
        self.assertNotRegex(role, r"host: [0-9]+[.][0-9]+[.][0-9]+[.][0-9]+")

    def test_host_commissioning_is_general_and_non_destructive(self):
        host = (ROOT / "bootstrap/commission-pve-runner.py").read_text()
        self.assertIn("PVE_NATIVE_SSH=PASS", host)
        self.assertIn("PVE_HARDWARE_AUDIT=PASS", host)
        self.assertIn("host_root_vmbr0", host)
        self.assertNotIn("qm destroy", host)
        self.assertNotIn("zpool destroy", host)
        audit = (ROOT / "playbooks/audit-pve-native.yml").read_text()
        self.assertIn("PVE_NATIVE_AUDIT_PASS", audit)
        self.assertIn("StrictHostKeyChecking=yes", audit)
        self.assertIn("infrastructure_changes: NONE", audit)

    def test_pve_deployment_and_cloudflare_planning_reconcile_first(self):
        for path in ("playbooks/plan-pve-media-vm.yml",
                     "playbooks/media-acceptance.yml",
                     "playbooks/plan-cloudflare-tunnel.yml",
                     "playbooks/audit-pve-access.yml"):
            with self.subTest(path=path):
                content = (ROOT / path).read_text()
                self.assertIn("name: site_inventory_prepare", content)
                self.assertIn("          - pve.yml", content)
                self.assertLess(content.index("name: site_inventory_prepare"),
                                content.index('file: "{{ inventory_dir }}/pve.yml"'))


if __name__ == "__main__":
    unittest.main()
