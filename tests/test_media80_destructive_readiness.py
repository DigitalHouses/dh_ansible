"""Media destructive recreate requires approved resource, disk, GPU and Plex policies."""
from pathlib import Path
import ast
import importlib.util
from unittest.mock import patch
import xml.etree.ElementTree as ET
import yaml

ROOT=Path(__file__).resolve().parents[1]


def test_recreate_declares_hard_cpu_and_memory_policy_on_vm_create_and_keep():
    source=(ROOT/"bootstrap/dh-pve-media-deploy.py").read_text()
    ast.parse(source)
    for literal in ("cpu_limit", "cpu_units", "ballooning", "zfs_arc_max_mb",
                    "transcode_cpu_limit", "MEDIA_PROTECTED_RESOURCE_POLICY_INVALID",
                    '"--cpulimit"', '"--cpuunits"', '"--balloon"', 
                    "MANAGED_ZFS_IMPORT_GUID_DRIFT"):
        assert literal in source
    assert source.index("old_guest_matches(vmid, site, data, managed=managed)") < source.index('qm("destroy", vmid')
    assert source.index('need(all(actual.get(key) == val for key, val in wanted.items())') < source.index("def ensure_config")


def test_import_guards_original_pool_guid_before_any_import():
    source=(ROOT/"roles/media_guest_setup/files/dh-media-storage.py").read_text()
    assert '"IMPORT_POOL_GUID_NOT_PINNED"' in source
    assert '"IMPORT_POOL_GUID_MISMATCH"' in source
    assert source.index("require(expected_guid is None or importable == expected_guid") < source.index("if command(*args, allow_failure=True) is None")


def test_shared_transcoder_limited_to_100_percent():
    tasks=(ROOT/"roles/media_guest_setup/tasks/cpu_guard.yml").read_text()
    assert "CPUQuota=100%" in tasks
    assert "100000 100000" in tasks
    assert "CPUQuota=150%" not in tasks


def test_zfs_arc_persistent_and_runtime_enforced():
    boot=(ROOT/"roles/media_guest_setup/tasks/zfs_arc_boot.yml").read_text()
    live=(ROOT/"roles/media_guest_setup/tasks/zfs_arc_runtime.yml").read_text()
    assert "zfs_arc_max=805306368" in boot
    assert "update-initramfs" in boot
    assert "/sys/module/zfs/parameters/zfs_arc_max" in live
    assert "MEDIA_ZFS_ARC_LIMIT_NOT_APPLIED" in live


def test_gpu_reboots_before_accepting_vaapi():
    p=ROOT/"roles/media_guest_setup/tasks/gpu_firmware_runtime.yml"
    tasks=yaml.safe_load(p.read_text())
    names=[t["name"] for t in tasks]
    assert names.index("Reboot only if GPU cannot initialize with newly installed firmware") < names.index(
        "Verify hardware VA-API initialized on the effective boot")
    assert any("ansible.builtin.reboot" in t for t in tasks)
    assert "MEDIA_INTEL_GPU_RUNTIME_NOT_FUNCTIONAL" in p.read_text()
    assert "declaring it wedged" in p.read_text()


def test_task80_protected_worker_pins_new_assets():
    installer=(ROOT/"bootstrap/semaphore-host-install.py").read_text()
    main=(ROOT/"roles/media_guest_setup/tasks/main.yml").read_text()
    for file in ("zfs_arc_boot.yml","zfs_arc_runtime.yml","gpu_firmware_runtime.yml",
                 "plex_autoscan.yml","dh-plex-scan-settings.py"):
        assert file in installer and file in main if file.endswith(".yml") else file in installer
    assert main.index("gpu_firmware_runtime.yml") < main.index("Install Plex Media Server")


def test_native_plex_scanner_reconciler_keeps_other_preferences_and_tokens(tmp_path,monkeypatch,capsys):
    script=ROOT/"roles/media_guest_setup/files/dh-plex-scan-settings.py"
    spec=importlib.util.spec_from_file_location("dh_test_plex_scan",script)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    prefs=tmp_path/"Preferences.xml"
    prefs.write_text('<Preferences PlexOnlineToken="SENSITIVE" OtherSetting="preserve" />')
    monkeypatch.setattr(module,"PREF",prefs)
    monkeypatch.setattr(module.pwd,"getpwnam",lambda _:type("Owner",(),{"pw_uid":prefs.stat().st_uid})())
    monkeypatch.setattr(module.subprocess,"run",lambda *a,**kw:type("Run",(),{"returncode":1})())
    monkeypatch.setattr("sys.argv",["scan","--mode","apply"])
    module.main()
    raw=prefs.read_text()
    for k,v in module.EXPECTED.items():
        assert ET.parse(prefs).getroot().get(k)==v
    assert ET.parse(prefs).getroot().get("PlexOnlineToken")=="SENSITIVE"
    assert ET.parse(prefs).getroot().get("OtherSetting")=="preserve"
    output=capsys.readouterr().out
    assert "SENSITIVE" not in output
