"""One Semaphore task 70: fail-closed audit plus explicitly authorized apply.

The shared household certificate identity and hostname are never optional.
The code must not put Cloudflare credentials or private keys in task output.
"""
from pathlib import Path
import json
import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_task_70_is_the_only_haos_mtls_task():
    catalog = json.loads((ROOT / "catalog/semaphore-tasks.json").read_text())
    tasks = [x for x in catalog["tasks"] if "HAOS" in x["title"] and "mTLS" in x["title"]]
    assert len(tasks) == 1
    assert tasks[0]["title"] == "70-Cloudflare · HAOS · mTLS · Настроить"
    assert tasks[0]["playbook"] == "playbooks/audit-cloudflare-haos-mtls.yml"


def test_preflight_strict_hostname_single_site_certificate_and_safe_apply():
    text = (ROOT / "playbooks/audit-cloudflare-haos-mtls.yml").read_text()
    plays = yaml.safe_load(text)
    assert len(plays) == 1 and plays[0]["hosts"] == "site_runner"
    assert plays[0]["become"] is False
    assert "site.id ~ '-ha-mtls.digitalhouses.vip'" in text
    assert "one-per-home-shared-with-residents" in text
    assert "haos.cloudflare.approved_for_publish | bool" in text
    assert "haos.cloudflare.mtls_required" in text
    assert "cloudflare_client_cert_waf" in text
    assert "ansible.builtin.uri:" in text
    assert "http://{{ haos.network.ipv4 }}:{{ haos.network.port }}/api/" in text
    assert "haos_mtls_public.status" in text
    assert "== 403" in text
    assert "dh-haos-mtls.path" in text
    assert "HAOS_MTLS:{{ haos_mtls_nonce.stdout }}" in text
    assert "dh-haos-mtls.request" in text
    assert "dh-haos-mtls.status" in text
    assert "ANONYMOUS" not in text
    assert "ansible.builtin.shell" not in text
    assert "ansible.builtin.script" not in text
    assert "ansible.builtin.git" not in text
    assert "validate_certs: true" in text
    assert "changed_when: false" in text


def test_workers_fixed_purpose_no_user_commands_or_insecure_cert_flags():
    runner = (ROOT / "bootstrap/dh-haos-mtls-runner.py").read_text()
    pve = (ROOT / "bootstrap/dh-cloudflare-pve-haos-mtls.py").read_text()
    for term in ("request.unlink()", "NONCE.fullmatch",
                 "SITE_BASE", "approved_for_publish",
                 "StrictHostKeyChecking=yes",
                 "PVE_IDENTITY_OR_RESULT_INVALID"):
        assert term in runner
    assert "shell=True" not in runner
    for term in ('cert_revoked', 'cert_subject_dn ne concat("CN=", http.host)',
                 "ONE_PER_SITE", "ANONYMOUS_NOT_BLOCKED",
                 "VALID_CERT_NOT_REACHING_HA", "ACCESS_EXACT_BYPASS_NOT_VERIFIED",
                 "WAF_SKIP_PRECEDES_MTLS_GUARD",
                 "HAOS_MTLS_RESULT=", "CLIENT_CERT_STATE_AMBIGUOUS"):
        assert term in pve
    assert "verify_mode = ssl.CERT_NONE" not in pve
    assert "check_hostname = False" not in pve
    assert "shell=True" not in pve
    assert "revoke" not in pve.lower() or "cert_revoked" in pve
