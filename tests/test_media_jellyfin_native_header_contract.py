"""Standalone tests for safe diagnostic retrieval and modern Jellyfin headers."""
import importlib.util
import io
from contextlib import redirect_stdout
from pathlib import Path
import tempfile
from unittest import mock
import yaml

ROOT = Path(__file__).resolve().parents[1]

def helper():
    src = ROOT / "roles/media_guest_setup/files/dh-jellyfin-bootstrap.py"
    spec = importlib.util.spec_from_file_location("jb_diags", src)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m

def test_jellyfin_authenticated_request_uses_modern_header_without_proxy():
    m = helper()
    token = "A" * 40
    requests = []
    class FakeResponse:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def read(self, n): return b"{}"
    class Opener:
        def open(self, request, timeout):
            requests.append((request, timeout))
            return FakeResponse()
    with mock.patch.object(m.urllib.request, "build_opener", return_value=Opener()) as factory:
        assert m.request("GET", "/Library/VirtualFolders", token=token) == {}
    request, timeout = requests[0]
    assert request.headers["Authorization"].endswith(', Token="' + token + '"')
    assert "X-Emby-Token" not in request.headers
    assert timeout == 12
    assert factory.call_args.args[0].proxies == {}
    assert m.STAGE[0] == "GET_LIBRARY"

def test_root_only_diagnostic_inspection_is_bounded_to_sanitized_stage():
    m = helper()
    with tempfile.TemporaryDirectory() as directory:
        m.DIAGNOSTIC = Path(directory) / "stage"
        m.STAGE[0] = "POST_STARTUP_USER"
        with mock.patch.object(m.os, "geteuid", return_value=0):
            m.record_diagnostic(RuntimeError("JELLYFIN_HTTP_403"))
        output = io.StringIO()
        with redirect_stdout(output):
            m.inspect_diagnostic()
        assert output.getvalue() == (
            "JELLYFIN_STAGE=POST_STARTUP_USER JELLYFIN_ERROR=JELLYFIN_HTTP_403\n")
        assert m.DIAGNOSTIC.stat().st_mode & 0o777 == 0o600
        m.clear_diagnostic()
        assert not m.DIAGNOSTIC.exists()

def test_ansible_exception_path_only_exposes_whitelisted_safe_diagnostic():
    tasks = yaml.safe_load(
        (ROOT / "roles/media_guest_setup/tasks/jellyfin_bootstrap.yml").read_text())
    task = next(x for x in tasks if x["name"] ==
                "Run Jellyfin setup with protected failure-stage reporting")
    assert task["block"][0]["no_log"] is True
    assert task["rescue"][0]["ansible.builtin.command"]["argv"] == [
        "/usr/local/sbin/dh-jellyfin-bootstrap", "--inspect-stage"]
    assert task["rescue"][0]["changed_when"] is False
    fail = task["rescue"][1]["ansible.builtin.fail"]["msg"]
    assert "MEDIA_JELLYFIN_BOOTSTRAP_BLOCKED" in fail
    assert "media_jellyfin_stage_diagnostic.rc == 0" in fail
