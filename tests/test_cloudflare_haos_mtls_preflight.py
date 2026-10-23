"""HAOS mTLS catalog task is read-only until certificate/WAF gates are verified."""
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_haos_mtls_preflight_is_non_mutating_and_site_scoped():
    text = (ROOT / "playbooks/audit-cloudflare-haos-mtls.yml").read_text()
    play = yaml.safe_load(text)
    assert len(play) == 1
    assert play[0]["hosts"] == "site_runner"
    assert play[0]["become"] is False
    assert "inventory_dir }}/haos.yml" in text
    assert "haos.cloudflare.mtls_required" in text
    assert "cloudflare_client_cert_waf" in text
    assert "/api/" in text
    assert "status | default(-1) | int == 401" in text
    assert "REQUIRED_BEFORE_APPLY" in text
    assert "AUDIT_ONLY_NOT_DEPLOYED" in text
    for forbidden in (
        "ansible.builtin.copy", "ansible.builtin.lineinfile",
        "ansible.builtin.shell", "ansible.builtin.script",
        "ansible.builtin.template", "ansible.builtin.systemd_service",
        "ansible.builtin.git", "POST", "PUT", "PATCH", "DELETE",
    ):
        assert forbidden not in text


def test_haos_mtls_is_a_separate_semaphore_catalog_task():
    import json
    catalog = json.loads((ROOT / "catalog/semaphore-tasks.json").read_text())
    matches = [task for task in catalog["tasks"]
               if task["playbook"] == "playbooks/audit-cloudflare-haos-mtls.yml"]
    assert len(matches) == 1
    assert matches[0]["title"].startswith("70-Cloudflare")
