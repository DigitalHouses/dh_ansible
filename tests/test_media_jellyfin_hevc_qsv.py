"""Protect N100 hardware HEVC decode from silent software fallback in Jellyfin."""
from pathlib import Path
import re

import yaml

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "roles/media_guest_setup/tasks/jellyfin_bootstrap.yml"


def test_hevc_is_enabled_in_the_existing_jellyfin_bootstrap_role():
    tasks = yaml.safe_load(PATH.read_text())
    by_name = {task["name"]: task for task in tasks}
    assert "Read current Jellyfin video acceleration configuration" in by_name
    assert "Enable Jellyfin QSV on untouched factory defaults and only missing HEVC decoder" in by_name
    assert "Require QSV HEVC hardware decoding to be enabled" in by_name
    block = by_name["Enable Jellyfin QSV on untouched factory defaults and only missing HEVC decoder"]
    assert "when" in block
    assert "hevc" in block["when"]
    assert "jellyfin.service" in str(block)
    assert "pgrep" in str(block)
    assert "smbd" not in str(block)
    assert "plexmediaserver" not in str(block)


def test_hevc_insertion_preserves_all_preexisting_codecs_and_is_idempotent():
    source = PATH.read_text()
    assert r"(<HardwareDecodingCodecs>\n)" in source
    assert r"\1    <string>hevc</string>\n" in source
    original = """<EncodingOptions>
  <HardwareAccelerationType>qsv</HardwareAccelerationType>
  <HardwareDecodingCodecs>
    <string>h264</string>
    <string>vc1</string>
    <string>vp9</string>
  </HardwareDecodingCodecs>
</EncodingOptions>"""
    updated, count = re.subn(
        r"(<HardwareDecodingCodecs>\n)",
        r"\1    <string>hevc</string>\n",
        original, count=1)
    assert count == 1
    assert updated.count("<string>hevc</string>") == 1
    assert "<string>h264</string>" in updated
    assert "<string>vc1</string>" in updated
    assert "<string>vp9</string>" in updated


def test_replay_does_not_restart_jellyfin_when_hevc_already_enabled():
    tasks = yaml.safe_load(PATH.read_text())
    block = next(t for t in tasks if t["name"] == "Enable Jellyfin QSV on untouched factory defaults and only missing HEVC decoder")
    assert "not in" in block["when"]
    assert any("state" in str(t.get("ansible.builtin.systemd_service", {})) for t in block["block"])
    assert any("state" in str(t.get("ansible.builtin.systemd_service", {})) for t in block["always"])


def test_fresh_jellyfin_default_none_qsv_is_managed_fail_closed():
    """A newly claimed Jellyfin 12 starts with none + h264/vc1; don't assume QSV."""
    tasks = yaml.safe_load(PATH.read_text())
    by_name = {step["name"]: step for step in tasks}
    validator = by_name["Verify Jellyfin hardware codec structure and managed accelerator"]
    checks = "\n".join(validator["ansible.builtin.assert"]["that"])
    assert "HardwareAccelerationType>none</HardwareAccelerationType>" in checks
    assert "JELLYFIN_ADMIN_AUTH=PASS" in checks
    assert "regex_search" in checks
    assert "h264" in checks and "vc1" in checks
    assert "MEDIA_JELLYFIN_QSV_CONFIGURATION_DRIFT" in str(validator)
    render = by_name["Reject missing or unexpected render device before Jellyfin configuration changes"]
    assert "MEDIA_JELLYFIN_QSV_RENDER_DEVICE_MISSING" in str(render)
    task = by_name["Enable Jellyfin QSV on untouched factory defaults and only missing HEVC decoder"]
    rewrite = next(x for x in task["block"] if x["name"].startswith("Enable QSV only"))
    assert rewrite["ansible.builtin.replace"]["regexp"] == r"(<HardwareAccelerationType>)none(</HardwareAccelerationType>)"
    assert rewrite["ansible.builtin.replace"]["replace"] == r"\1qsv\2"
    assert rewrite["ansible.builtin.replace"]["backup"] is True
    assert "none" in rewrite["when"]
    assert "HardwareAccelerationType>qsv</HardwareAccelerationType>" in str(
        by_name["Require QSV HEVC hardware decoding to be enabled"]
    )


def test_repeat_run_only_restarts_jellyfin_for_actual_drift():
    tasks = yaml.safe_load(PATH.read_text())
    block = next(t for t in tasks
                 if t["name"] == "Enable Jellyfin QSV on untouched factory defaults and only missing HEVC decoder")
    assert "HardwareAccelerationType>none" in block["when"]
    assert "<string>hevc</string>" in block["when"]
    assert "pgrep" in str(block)
    assert "state': 'started" not in str(block["block"])
    assert any("jellyfin.service" in str(t) for t in block["always"])
