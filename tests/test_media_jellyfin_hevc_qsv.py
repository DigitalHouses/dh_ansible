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
    assert "Enable only missing Jellyfin HEVC hardware decoder" in by_name
    assert "Require HEVC hardware decoding to be enabled" in by_name
    block = by_name["Enable only missing Jellyfin HEVC hardware decoder"]
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
    block = next(t for t in tasks if t["name"] == "Enable only missing Jellyfin HEVC hardware decoder")
    assert "not in" in block["when"]
    assert any("state" in str(t.get("ansible.builtin.systemd_service", {})) for t in block["block"])
    assert any("state" in str(t.get("ansible.builtin.systemd_service", {})) for t in block["always"])
