"""Offline safety and ownership gates for the PVE Access template."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(name):
    return (ROOT / name).read_text()


def test_pve_access_audit_is_fixed_purpose_and_read_only():
    source = read("playbooks/audit-pve-access.yml")
    for required in (
        "StrictHostKeyChecking=yes",
        "UserKnownHostsFile=",
        "ssh_root_id.stdout",
        "tls_identity.stdout",
        "pve_escrow.identity_schema",
        "ansible-audit@pve",
        "ansible-writer@pve",
        "ansible-linux",
        "PVEAPIToken=",
        "no_log: true",
    ):
        assert required in source
    assert "method: GET" in source
    assert "method: POST" not in source
    assert "method: DELETE" not in source
    assert "method: PUT" not in source
    assert "write_token_secret" not in source


def test_semaphore_request_has_unique_nonce_and_no_privilege_escalation():
    source = read("playbooks/request-pve-access-audit.yml")
    assert "become: false" in source
    assert "/proc/sys/kernel/random/uuid" in source
    assert "AUDIT:{{ pve_audit_request_id.stdout }}" in source
    assert "PASS:' ~ pve_audit_request_id.stdout" in source
    assert "FAILED:' ~ pve_audit_request_id.stdout" in source
    assert "dh-pve-access-audit.request" in source


def test_root_worker_is_pinned_to_specific_readonly_playbook():
    worker = read("bootstrap/pve-access-audit-request.sh")
    assert "set -Eeuo pipefail" in worker
    assert "semaphore:semaphore:600" in worker
    assert "dh-pve-access-audit.request" in worker
    assert "playbooks/audit-pve-access.yml" in worker
    assert "sudo" not in worker
    assert 'report RUNNING' in worker and 'report PASS' in worker
    host = read("bootstrap/semaphore-host-install.py")
    assert '"playbooks/audit-pve-access.yml"' in host
    assert '"playbooks/request-pve-access-audit.yml"' in host
    assert 'stage_pve_access_audit(checkout)' in host
    assert '"--reconcile-restored", "--apply"' in host
    seeder = read("bootstrap/semaphore-project.py")
    assert "40-Проверка доступов PVE" in seeder
