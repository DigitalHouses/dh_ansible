"""Regression tests: Debian cloud VM must get QGA before QGA trust probe.

No test interacts with a real VM or physical drive.
"""
import ast
import importlib.util
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "bootstrap/dh-pve-media-deploy.py"
PLAY = ROOT / "playbooks/plan-pve-media-vm.yml"


def load():
    spec = importlib.util.spec_from_file_location("dh_media_qga_firstboot", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_cloudinit_agent_packages_are_commissioned_before_first_vm_start():
    script = SCRIPT.read_text()
    ast.parse(script)
    body = script.split("def ensure_config(", 1)[1].split("def guest_exec(", 1)[0]
    assert "ensure_qga_cloud_init(vmid, data)" in body
    apply = script.split("def apply(site,", 1)[1].split("def ensure_backup_storage", 1)[0]
    assert apply.index("ensure_config(vmid, site, data)") < apply.index('qm("start", vmid')
    vendor = script.split("def ensure_qga_cloud_init(", 1)[1].split(
        "def qga_running(", 1)[0]
    for required in ("#cloud-config", "qemu-guest-agent", "systemctl",
                     "enable, --now", "instance-id:", "--cicustom",
                     '"cloudinit", "update"', "pvesm", "snippets"):
        assert required in vendor
    assert "user=local:snippets" not in vendor
    assert 'Path("/var/lib/vz/snippets")' in vendor


def test_qga_host_key_still_comes_only_from_authenticated_pve_agent():
    source = PLAY.read_text()
    tasks = yaml.safe_load(source)[1]["tasks"]
    names = [t["name"] for t in tasks]
    assert names.index("Provision only the declared PVE media VM by native Ansible SSH") < (
        names.index("Read new Media guest ED25519 host key over commissioned PVE QEMU Guest Agent"))
    assert "StrictHostKeyChecking=no" not in source
    assert "StrictHostKeyChecking=accept-new" not in source
    assert "ssh-keyscan" in source


def test_cicustom_normalization_is_independent_of_proxmox_option_order():
    m = load()
    expected = m.qga_cicustom_expected(2232)
    for raw in (
        "vendor=" + expected["vendor"] + ",meta=" + expected["meta"],
        "meta=" + expected["meta"] + ",vendor=" + expected["vendor"],
    ):
        with patch.object(m, "config", return_value={"cicustom": raw}):
            assert m.qga_cicustom(2232) == expected
    with patch.object(m, "config", return_value={}):
        assert m.qga_cicustom(2232) is None


@pytest.mark.parametrize("unsafe", [
    "user=foreign:snippets/init.yaml",
    "meta=foreign:snippets/other.yaml",
    "meta=foo,meta=bar",
    "vendor=foo,meta=bar,network=other",
])
def test_foreign_cloudinit_snippets_are_never_silently_overwritten(unsafe):
    m = load()
    with patch.object(m, "config", return_value={"cicustom": unsafe}):
        try:
            got = m.qga_cicustom(2232)
        except m.Blocked:
            return
        assert got != m.qga_cicustom_expected(2232)


def fake_apply(*, qga=False, cicustom=None):
    m = load()
    name = "mediaserver-serzhan"
    data = {"name": name, "vmid": 2232, "resources": {}, "storage": {}}
    calls = []
    status = {"value": "status: running"}
    def qm(*args, **kw):
        calls.append(args)
        if args[0] == "shutdown":
            status["value"] = "status: stopped"
        elif args[0] == "start":
            status["value"] = "status: running"
        elif args[0] == "status":
            return status["value"]
        return ""
    with patch.object(m, "site_data", return_value=(data, {})), \
         patch.object(m, "check", return_value=(2232, name, "192.168.11.232/24")), \
         patch.object(m, "reconcile_media_vm_lifecycle"), \
         patch.object(m.Path, "exists", return_value=True), \
         patch.object(m, "config", return_value={
             "name": name, "description": m.MANAGED_MEDIA_DESCRIPTION}), \
         patch.object(m, "ensure_lifecycle"), \
         patch.object(m, "old_guest_matches") as gate, \
         patch.object(m, "qga_running", return_value=qga), \
         patch.object(m, "qga_cicustom", return_value=cicustom), \
         patch.object(m, "ensure_qga_cloud_init") as cloud, \
         patch.object(m, "qm", side_effect=qm):
        m.apply("serzhan-home", "keep", "erase")
    return calls, gate, cloud


def test_running_vm_with_qga_is_never_rebooted_or_recreated():
    calls, gate, cloud = fake_apply(qga=True)
    assert not any(x[0] in ("shutdown", "start", "destroy") for x in calls)
    gate.assert_not_called()
    cloud.assert_not_called()


def test_first_failed_qga_recovery_restarts_only_managed_existing_vm():
    calls, gate, cloud = fake_apply(qga=False, cicustom=None)
    assert [x[0] for x in calls if x[0] in ("shutdown", "start", "destroy")] == [
        "shutdown", "start"]
    gate.assert_called_once()
    cloud.assert_called_once()


def test_second_retry_waits_for_cloudinit_instead_of_reboot_loop():
    m = load()
    calls, gate, cloud = fake_apply(
        qga=False, cicustom=m.qga_cicustom_expected(2232))
    assert not any(x[0] in ("shutdown", "start", "destroy") for x in calls)
    gate.assert_called_once()
    cloud.assert_not_called()
