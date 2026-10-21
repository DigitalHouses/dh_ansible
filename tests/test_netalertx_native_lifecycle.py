"""Static fail-closed contracts for the staged, native NetAlertX lifecycle."""
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def _yaml(path):
    return yaml.safe_load((ROOT / path).read_text())


def test_worker_is_not_registered_as_a_semaphore_task():
    import json
    catalog = json.loads((ROOT / "catalog/semaphore-tasks.json").read_text())
    matches = [task for task in catalog["tasks"] if "NetAlertX" in task["title"]]
    assert len(matches) == 1
    assert matches[0]["playbook"] == "playbooks/request-netalertx-install.yml"
    worker = _yaml("playbooks/deploy-netalertx-pve-from-worker.yml")[0]
    assert worker["hosts"] == "site_runner"
    assert worker["become"] is False
    assert "netalertx_pve_lxc" in str(worker["roles"])
    assert "netalertx_pve_worker_approved" in str(worker["pre_tasks"])
    assert "StrictHostKeyChecking=yes" in str(worker["pre_tasks"])


def test_pve_role_requires_identity_and_approval_and_creates_only_stopped_lxc():
    tasks = _yaml("roles/netalertx_pve_lxc/tasks/main.yml")
    text = (ROOT / "roles/netalertx_pve_lxc/tasks/main.yml").read_text()
    assert tasks and isinstance(tasks, list)
    for guard in (
        "provision_approved",
        "dhcp_exclusion_verified",
        "protected_vmids",
        "template_sha256",
        "netalertx_pve_worker_approved",
    ):
        assert guard in text
    assert "pct, config" in text
    assert "qm, status" in text
    assert "link_down=1" in text
    assert "--onboot" in text and "--protection" in text
    assert "pct, destroy" not in text
    assert "pct, stop" not in text
    assert "pct, start" not in text
    assert "state: absent" not in text
    assert "delegate_to: dh_netalertx_pve" in text


def test_graphql_isolation_is_loaded_before_native_app_and_requires_service():
    steps = _yaml("roles/netalertx_native/tasks/main.yml")
    order = [x["ansible.builtin.import_tasks"] for x in steps]
    assert order.index("packages.yml") < order.index("firewall.yml")
    assert order.index("firewall.yml") < order.index("install.yml")
    assert order.index("firewall.yml") < order.index("verify.yml")
    packages = str(_yaml("roles/netalertx_native/tasks/packages.yml"))
    assert "nftables" in packages
    firewall = _yaml("roles/netalertx_native/tasks/firewall.yml")
    commands = [task.get("ansible.builtin.command", {}).get("argv") for task in firewall]
    assert ["nft", "-c", "-f", "/etc/nftables.conf"] in commands
    assert ["nft", "list", "chain", "inet", "dh_netalertx", "input"] in commands
    rule = (ROOT / "roles/netalertx_native/templates/netalertx-nftables.conf.j2").read_text()
    assert 'iifname != "lo"' in rule
    assert "tcp dport {{ netalertx_native_api_port }} drop" in rule
    unit = (ROOT / "roles/netalertx_native/templates/netalertx.service.j2").read_text()
    assert "Requires=netalertx-runtime.service nftables.service" in unit


def test_pve_description_matches_only_canonical_record_with_optional_encoded_lf():
    text = (ROOT / "roles/netalertx_pve_lxc/tasks/main.yml").read_text()
    assert text.count("~ '%0A'") == 2
    assert text.count("('description: DigitalHouses native NetAlertX site=' ~ site.id)") == 2
    assert text.count(" in netalertx_pve_existing.stdout_lines") >= 2
    assert text.count(" in netalertx_pve_final.stdout_lines") >= 2


def test_add_host_is_bookkeeping_and_onboot_requires_post_acceptance():
    plays = _yaml("playbooks/deploy-netalertx-pve-from-worker.yml")
    assert len(plays) == 3
    add_hosts = [
        task
        for key in ("pre_tasks", "post_tasks")
        for task in plays[0].get(key, [])
        if "ansible.builtin.add_host" in task
    ]
    assert len(add_hosts) == 2
    assert all(t.get("changed_when") is False for t in add_hosts)
    assert plays[2]["hosts"] == "site_runner"
    phase = str(plays[2]["tasks"])
    assert "api_lan_isolation_verified" in phase
    assert "protection: 1" in phase
    assert "netalertx.lifecycle.onboot" in phase
    assert "pct" in phase and "onboot" in phase
