"""Jellyfin startup diagnostic never discloses passwords, tokens or arbitrary errors."""
import importlib.util
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "roles/media_guest_setup/files/dh-jellyfin-bootstrap.py"

def helper():
    spec = importlib.util.spec_from_file_location("dh_jellyfin_diagnostic", SOURCE)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m

def test_stage_tracks_fixed_known_endpoints():
    m = helper()
    assert m.stage_name("POST", "/Startup/User") == "POST_STARTUP_USER"
    assert m.stage_name("POST", "/Startup/Complete") == "POST_STARTUP_COMPLETE"
    assert m.stage_name("POST", "/Users/AuthenticateByName") == "POST_OWNER_AUTH"
    assert m.stage_name("GET", "/Library/VirtualFolders?name=hidden") == "GET_LIBRARY"
    assert m.stage_name("GET", "/Unknown/must-not-leak") == "GET_OTHER_FIXED_ENDPOINT"

def test_diagnostic_accepts_only_predefined_safe_codes():
    m = helper()
    m.STAGE[0] = "POST_STARTUP_USER"
    assert m.sanitized_diagnostic(RuntimeError("JELLYFIN_HTTP_400")) == (
        "JELLYFIN_STAGE=POST_STARTUP_USER JELLYFIN_ERROR=JELLYFIN_HTTP_400\n")
    for secret in ('passw0rd', 'Bearer secret-token', 'JELLYFIN_HTTP_400 password',
                   'ABCDEFGH', 'PW123456', 'PASSWORD_RESET'):
        assert secret not in m.sanitized_diagnostic(RuntimeError(secret))
        assert "JELLYFIN_ERROR=UNCLASSIFIED" in m.sanitized_diagnostic(
            RuntimeError(secret))
    m.STAGE[0] = "PASSWORD12"
    assert "JELLYFIN_STAGE=UNKNOWN_STAGE" in m.sanitized_diagnostic(
        RuntimeError("ADMIN_PASSPORT_INVALID"))

def test_diagnostic_file_is_root_owned_private_and_never_prints_credentials(tmp_path):
    m = helper()
    m.DIAGNOSTIC = tmp_path / "status"
    m.STAGE[0] = "POST_STARTUP_USER"
    with mock.patch.object(m.os, "geteuid", return_value=0):
        m.record_diagnostic(RuntimeError("JELLYFIN_HTTP_400"))
    assert m.DIAGNOSTIC.read_text() == (
        "JELLYFIN_STAGE=POST_STARTUP_USER JELLYFIN_ERROR=JELLYFIN_HTTP_400\n")
    assert m.DIAGNOSTIC.stat().st_mode & 0o777 == 0o600
    with mock.patch.object(m.os, "geteuid", return_value=0):
        m.record_diagnostic(RuntimeError("JELLYFIN_HTTP_401 credential-exposed"))
    assert "credential-exposed" not in m.DIAGNOSTIC.read_text()
    m.clear_diagnostic()
    assert not m.DIAGNOSTIC.exists()

def test_symlink_cannot_be_overwritten(tmp_path):
    m = helper()
    m.DIAGNOSTIC = tmp_path / "status"
    target = tmp_path / "victim"
    target.write_text("unchanged")
    m.DIAGNOSTIC.symlink_to(target)
    with mock.patch.object(m.os, "geteuid", return_value=0):
        m.record_diagnostic(RuntimeError("JELLYFIN_HTTP_400"))
    assert target.read_text() == "unchanged"
