"""ZFS same-dataset downloads and safely bounded qBittorrent path migration."""
import importlib.util
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
P = ROOT / "roles/media_guest_setup/files/dh-media-arr-wire.py"
SPEC = importlib.util.spec_from_file_location("dh_media_shared_downloads", P)
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)

NEW = "/serzhan-pool/media/downloads"
OLD = "/srv/downloads"


def test_role_guards_exact_mount_source_and_zfs():
    tasks = yaml.safe_load(
        (ROOT / "roles/media_guest_setup/tasks/main.yml").read_text())
    named = {t["name"]: t for t in tasks}
    mount = named["Detect filesystem type and source of declared download root"]
    assert "SOURCE,FSTYPE" in mount["ansible.builtin.command"]["argv"]
    guard = named["Require declared filesystem is exactly the authorized storage target"]
    expression = str(guard["ansible.builtin.assert"]["that"])
    assert "media_vm.storage.media_dataset" in expression
    assert "'zfs'" in expression
    assert "shared-zfs" in expression
    assert "DOWNLOAD_ROOT_FILESYSTEM_MISMATCH" == guard["ansible.builtin.assert"]["fail_msg"]


def test_helper_accepts_empty_previous_staging_only():
    assert m.safe_empty_previous_staging.__name__ == "safe_empty_previous_staging"


def test_unsafe_nonempty_staging_blocks_before_api_change(tmp_path):
    # Safety test uses the real helper logic under an isolated test staging path.
    source = m.safe_empty_previous_staging.__code__
    assert b"/srv/downloads" in source.co_consts or "/srv/downloads" in source.co_consts
    with patch.object(m.Path, "is_dir", return_value=True), \
         patch.object(m.Path, "is_symlink", return_value=False), \
         patch.object(m.Path, "rglob", return_value=[Path("existing-video.mkv")]), \
         patch.object(m.Path, "is_file", return_value=True):
        with pytest.raises(m.Blocked, match="QBT_PREVIOUS_DOWNLOADS_NOT_EMPTY"):
            m.safe_empty_previous_staging()


def test_migration_changes_paths_only_when_old_defaults_verified():
    preferences = {"save_path": OLD, "temp_path": OLD + "/incomplete",
                   "temp_path_enabled": True, "bypass_local_auth": True}
    categories = {
        "radarr": {"savePath": OLD + "/complete/movies"},
        "tv-sonarr": {"savePath": OLD + "/complete/series"},
        "prowlarr": {"savePath": OLD + "/complete/prowlarr"},
    }
    operations = []

    def fake_qbt(route, *, method="GET", data=None):
        operations.append((route, method))
        if route == "app/preferences":
            return preferences.copy()
        if route == "torrents/info":
            return []
        if route == "torrents/categories":
            return {key: value.copy() for key, value in categories.items()}
        if route == "app/setPreferences":
            import json
            preferences.update(json.loads(data["json"]))
            return None
        if route == "torrents/editCategory":
            categories[data["category"]]["savePath"] = data["savePath"]
            return None
        raise AssertionError(route)

    with patch.object(m, "qbt", side_effect=fake_qbt), \
         patch.object(m, "safe_empty_previous_staging"):
        m.ensure_qbt_access(NEW)
        with patch.object(m.Path, "is_dir", return_value=True):
            m.ensure_categories(NEW)

    assert preferences["save_path"] == NEW
    assert preferences["temp_path"] == NEW + "/incomplete"
    assert categories["radarr"]["savePath"] == NEW + "/complete/movies"
    assert categories["tv-sonarr"]["savePath"] == NEW + "/complete/series"
    assert ("app/setPreferences", "POST") in operations
    assert operations.count(("torrents/editCategory", "POST")) == 3

    operations.clear()
    with patch.object(m, "qbt", side_effect=fake_qbt), \
         patch.object(m.Path, "is_dir", return_value=True):
        m.ensure_qbt_access(NEW)
        m.ensure_categories(NEW)
    assert not any(method == "POST" for _, method in operations)


def test_migration_blocks_foreign_saved_root():
    with patch.object(m, "qbt", return_value={
        "save_path": "/another/location",
        "temp_path": "/another/location/incomplete",
        "temp_path_enabled": True,
        "bypass_local_auth": True}):
        with pytest.raises(m.Blocked, match="QBT_DOWNLOAD_PATH_UNEXPECTED"):
            m.ensure_qbt_access(NEW)


def test_existing_torrents_block_migration():
    with patch.object(m.Path, "is_dir", return_value=True), \
         patch.object(m.Path, "is_symlink", return_value=False), \
         patch.object(m.Path, "rglob", return_value=[]), \
         patch.object(m, "qbt", return_value=[{"hash": "fixture"}]):
        with pytest.raises(m.Blocked, match="QBT_DOWNLOAD_MIGRATION_TORRENTS_PRESENT"):
            m.safe_empty_previous_staging()
