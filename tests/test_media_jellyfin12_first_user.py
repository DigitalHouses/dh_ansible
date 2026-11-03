"""Jellyfin 12 requires GET /Startup/User before POST updates the first user."""
import importlib.util
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "roles/media_guest_setup/files/dh-jellyfin-bootstrap.py"


def module():
    spec = importlib.util.spec_from_file_location("dh_jellyfin_first_user", HELPER)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def run_bootstrap(first_user):
    m = module()
    calls = []
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "digitalhouses" / "credentials" / "pilot.txt"
        path.parent.mkdir(parents=True)
        path.write_text("username=pilot\npassword=demo1234\n")
        path.chmod(0o640)
        original_stat = Path.stat
        def stat_override(obj, *args, **kwargs):
            s = original_stat(obj, *args, **kwargs)
            if obj == path:
                return SimpleNamespace(st_uid=0,
                    st_mode=stat.S_IFREG | 0o640)
            return s
        public_reads = [0]
        def request(method, url, payload=None, token=None):
            calls.append((method, url))
            if (method, url) == ("GET", "/System/Info/Public"):
                public_reads[0] += 1
                return {"StartupWizardCompleted": public_reads[0] > 1}
            if (method, url) == ("GET", "/Startup/Configuration"):
                return {"ServerName": "Test", "UICulture": "en-US"}
            if (method, url) == ("GET", "/Startup/User"):
                return first_user
            if (method, url) == ("POST", "/Users/AuthenticateByName"):
                return {"AccessToken": "fake-token-with-safe-test-format-abc"}
            return {}
        with patch.object(m.os, "geteuid", return_value=0), \
             patch.object(m.Path, "stat", stat_override), \
             patch.object(m, "request", side_effect=request), \
             patch.object(m, "reconcile_libraries") as libs, \
             patch.object(m, "clear_diagnostic"):
            try:
                m.bootstrap(path, Path("/demo-pool/media"))
                error = None
            except RuntimeError as exc:
                error = str(exc)
        return calls, error, libs.call_count


def test_first_user_initialized_before_password_posted():
    calls, error, library_calls = run_bootstrap({"Name": "initial-owner"})
    assert error is None
    assert library_calls == 1
    assert calls.index(("GET", "/Startup/User")) < calls.index(
        ("POST", "/Startup/User"))
    assert ("POST", "/Startup/Complete") in calls
    assert ("POST", "/Users/AuthenticateByName") in calls


@pytest.mark.parametrize("invalid", [None, {}, {"Name": ""}, {"Name": 17}])
def test_incomplete_first_user_blocks_without_password_or_wizard_completion(invalid):
    calls, error, library_calls = run_bootstrap(invalid)
    assert error == "JELLYFIN_STARTUP_FIRST_USER_INVALID"
    assert ("POST", "/Startup/User") not in calls
    assert ("POST", "/Startup/Complete") not in calls
    assert library_calls == 0
