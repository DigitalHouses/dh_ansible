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
        self.assertNotIn("192.168.11.6", host)
        self.assertNotIn("192.168.11.30", host)

    def test_site_inventory_never_trusts_fleet_ip_over_local_observation(self):
        role = (ROOT / "roles/site_inventory_prepare/tasks/main.yml").read_text()
        self.assertIn("PVE_RUNTIME_ENDPOINT_MISSING", role)
        self.assertIn("pve-runtime.yml", role)
        self.assertIn("site_pve_source.content", role)
        self.assertIn("combine({'host': pve_observed.ipv4})", role)
        self.assertIn("to_nice_yaml", role)
        self.assertNotIn("192.168.11.6", role)
        self.assertNotIn("192.168.11.30", role)

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
