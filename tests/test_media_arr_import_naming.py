"""New media is named for Plex matching during future Arr imports.

No existing files or folders are renamed; only API naming toggles are updated.
"""
import importlib.util
from pathlib import Path
from unittest.mock import patch

import pytest

BASE = Path(__file__).resolve().parents[1]
SOURCE = BASE / "roles/media_guest_setup/files/dh-media-arr-wire.py"


def module():
    spec = importlib.util.spec_from_file_location("media_arr_naming", SOURCE)
    obj = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obj)
    return obj


def settings(app, enabled=False):
    if app == "radarr":
        return {
            "id": 1,
            "renameMovies": enabled,
            "standardMovieFormat": "{Movie Title} ({Release Year}) {Quality Full}",
            "movieFolderFormat": "{Movie Title} ({Release Year})",
            "replaceIllegalCharacters": True,
            "colonReplacementFormat": 4,
        }
    return {
        "id": 1,
        "renameEpisodes": enabled,
        "standardEpisodeFormat": "{Series Title} - S{season:00}E{episode:00} - {Episode Title} {Quality Full}",
        "seriesFolderFormat": "{Series Title}",
        "seasonFolderFormat": "Season {season}",
        "replaceIllegalCharacters": True,
    }


@pytest.mark.parametrize("app,field", [
    ("radarr", "renameMovies"),
    ("sonarr", "renameEpisodes"),
])
def test_disables_unstructured_release_names_only_for_future_imports(app, field):
    m = module()
    state = settings(app)
    writes = []

    def fake_api(target, endpoint, **kwargs):
        assert target == app
        if kwargs.get("method") == "PUT":
            assert endpoint == "config/naming/1"
            assert len(writes) == 0
            payload = kwargs["data"]
            assert payload[field] is True
            assert payload.keys() == state.keys()
            for k, v in state.items():
                if k != field:
                    assert payload[k] == v
            writes.append(payload)
            state.update(payload)
            return state.copy()
        assert endpoint == "config/naming"
        assert kwargs == {}
        return state.copy()

    with patch.object(m, "api", side_effect=fake_api):
        m.ensure_import_naming(app)
        m.ensure_import_naming(app)
    assert len(writes) == 1
    assert state[field] is True


def test_when_naming_is_already_enabled_no_writes():
    m = module()
    for app in ("radarr", "sonarr"):
        state = settings(app, True)
        with patch.object(m, "api", return_value=state) as fake:
            m.ensure_import_naming(app)
        assert fake.call_count == 2
        assert all(call.kwargs.get("method") != "PUT" for call in fake.call_args_list)


def test_missing_plex_compatible_tokens_blocks_without_writes():
    m = module()
    for app in ("radarr", "sonarr"):
        state = settings(app)
        field = "standardMovieFormat" if app == "radarr" else "standardEpisodeFormat"
        state[field] = "{Release Group}"
        with patch.object(m, "api", return_value=state) as fake:
            with pytest.raises(m.Blocked, match="NAMING_.*_TEMPLATE_NOT_PLEX_COMPATIBLE"):
                m.ensure_import_naming(app)
        assert fake.call_count == 1


def test_drift_in_template_after_save_fails_closed():
    m = module()
    current = settings("radarr")
    updated = settings("radarr", True)
    updated["standardMovieFormat"] = "{Movie Title} ({Release Year})"
    with patch.object(m, "api", side_effect=[current, updated, updated]):
        with pytest.raises(m.Blocked, match="NAMING_SETTINGS_POSTCHECK_DRIFT_radarr"):
            m.ensure_import_naming("radarr")


def test_script_never_invokes_existing_media_file_rename_or_move():
    code = SOURCE.read_text()
    segment = code.split("def ensure_import_naming(app:", 1)[1].split("\ndef main():", 1)[0]
    for forbidden in ('/command', "renamefiles", "renameMovieFiles", "moveFiles",
                      "os.rename", "Path.rename", "shutil.move",
                      "filebrowser", "moviefile/editor"):
        assert forbidden not in segment
    assert '--naming-only' in code
    assert 'ensure_import_naming(app)' in code
    policy = (BASE / "roles/media_guest_setup/files/dh-plex-scan-settings.py").read_text()
    assert '"FSEventLibraryUpdatesEnabled":"1"' in policy
    assert '"FSEventLibraryPartialScanEnabled":"1"' in policy
    assert '"ScheduledLibraryUpdatesEnabled":"1"' in policy
    assert '"ScheduledLibraryUpdateInterval":"900"' in policy
