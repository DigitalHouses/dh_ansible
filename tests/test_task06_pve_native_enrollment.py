"""Task 06 native PVE enrollment contracts; no live root password or PVE write.

This gate proves Bootstrap only seeds PUBLIC trust; PVE root key authorization
is exclusively Task 06 and protected by strict SSH host key plus API gate.
"""
from pathlib import Path
import ast
import re

import yaml


ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP = (ROOT / "bootstrap/install.sh").read_text()
GUEST = (ROOT / "bootstrap/semaphore-guest-install.py").read_text()
TRUST = (ROOT / "bootstrap/seed-pve-public-trust.py").read_text()
CHECK = (ROOT / "playbooks/check-site-credentials.yml").read_text()
ENROLL = (ROOT / "scripts/pve-authorize-ssh-key.py").read_text()


def plays():
    return yaml.safe_load(CHECK)


def task_by_name(play, phrase):
    return next(t for t in play["tasks"] if phrase in t["name"])


def test_there_is_no_task08_and_single_task06_commissions():
    catalog = __import__("json").loads(
        (ROOT / "catalog/semaphore-tasks.json").read_text()
    )
    titles = [t["title"] for t in catalog["tasks"]]
    assert "06-Проверить credentials объекта" in titles
    assert not any(t.startswith("08-") for t in titles)
    p = plays()
    assert [q["hosts"] for q in p] == [
        "site_runner", "dh_pve_first_enrollment", "dh_pve_key_probe",
    ]
    assert all(q["gather_facts"] is False for q in p)


def test_bootstrap_public_trust_seed_is_same_host_and_before_done():
    assert "seed-pve-public-trust.py" in BOOTSTRAP
    assert BOOTSTRAP.index('echo "CT_CREATE=PASS"') < BOOTSTRAP.index(
        "seed-pve-public-trust.py")
    assert BOOTSTRAP.index("seed-pve-public-trust.py") < BOOTSTRAP.index(
        'echo "BOOTSTRAP_FRESH_DONE=PASS"')
    assert "TRUST_TMP" in BOOTSTRAP
    assert "commission-pve-runner.py" in TRUST
    for forbidden in ('old["ensure_guest_key"]', 'old["authorize_host_key"]',
                      'old["verify_ssh"]'):
        assert forbidden not in TRUST
    for required in ('old["observed_host"]', 'old["runner_identity"]',
                     'old["read_live_certificate"]',
                     'old["install_guest_trust"]',
                     "PVE_PUBLIC_TRUST_SEED=PASS"):
        assert required in TRUST
    assert "sshpass" in GUEST


def test_password_is_only_used_in_protected_native_transport():
    p = plays()
    local_tasks = p[0]["tasks"]
    gate = task_by_name(p[0], "Block PVE enrollment")
    assert "dh_pve_status == 'READY'" in str(gate)
    add = task_by_name(p[0], "Register password-based first connection")
    attrs = add["ansible.builtin.add_host"]
    assert attrs["ansible_user"] == "root"
    assert attrs["ansible_password"] == "{{ DH_PVE_ROOT_PASSWORD }}"
    assert add["no_log"] is True
    assert "dh_pve_existing_ssh" in str(add["when"])
    assert "StrictHostKeyChecking=yes" in attrs["ansible_ssh_common_args"]
    assert "UserKnownHostsFile=/var/lib/semaphore/.ssh/dh_pve_known_hosts" in (
        attrs["ansible_ssh_common_args"]
    )
    assert "sshpass" not in CHECK
    assert "DH_PVE_ROOT_PASSWORD" not in ENROLL
    enrollment = p[1]["tasks"][0]
    assert enrollment["no_log"] is True
    assert "scripts/pve-authorize-ssh-key.py" in (
        enrollment["ansible.builtin.script"]
    )


def test_rerun_prefers_pinned_site_key_and_only_first_run_password():
    p = plays()
    probe = task_by_name(p[0], "Probe existing pin-verified")
    assert probe["failed_when"] is False
    assert "StrictHostKeyChecking=yes" in str(probe)
    assert "BatchMode=yes" in str(probe)
    check = task_by_name(p[0], "Register strict public-key")
    assert "ansible_password" not in check["ansible.builtin.add_host"]
    assert "ansible_ssh_private_key_file" in check["ansible.builtin.add_host"]
    assert "BatchMode=yes" in str(check)


def test_key_enrollment_is_identity_bound_and_pve_symlink_safe():
    ast.parse(ENROLL)
    assert 'Path("/etc/pve/user.cfg")' in ENROLL
    assert "node == expected_node and ip == expected_ip" in ENROLL
    assert 'Path("/etc/pve/priv/authorized_keys")' in ENROLL
    assert "PVE_ROOT_AUTH_KEYS_UNEXPECTED_LINK" in ENROLL
    assert "PVE_NATIVE_SSH_KEY=" in ENROLL
    assert "UNEXPECTED_PVE_PUBLIC_KEY" in ENROLL
    assert "DUPLICATED_PVE_ROOT_SSH_KEY" in ENROLL
    assert "with dest.open(\"a\"" in ENROLL


def test_readiness_is_only_after_key_based_remote_root_identity():
    p = plays()
    final = p[2]
    names = [t["name"] for t in final["tasks"]]
    assert names.index("Verify remote SSH account is root") < names.index(
        "Report full PVE credential and infrastructure readiness")
    assert names.index("Verify expected root privileges and exact live node") < (
        names.index("Report full PVE credential and infrastructure readiness")
    )
    msg = final["tasks"][-1]["ansible.builtin.debug"]["msg"]
    assert msg["overall"] == "READY"
    assert msg["pve_ssh"]["status"] == "READY"
    assert msg["password_or_ticket_logged"] is False


def test_bootstrap_tiny_contract_and_python_syntax():
    assert len(BOOTSTRAP.splitlines()) <= 130
    assert re.findall(r"=== ([1-4])/4", BOOTSTRAP) == ["1","2","3","4"]
    ast.parse(TRUST)
