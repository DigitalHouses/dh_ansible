"""Filestash admin API diagnostics must be stage-specific and secret-free."""
import importlib.util
import io
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError, URLError

SOURCE = Path(__file__).resolve().parents[1] / (
    "roles/media_guest_setup/files/dh-filestash-smb-config.py")


def helper():
    spec = importlib.util.spec_from_file_location("dh_filestash_probe", SOURCE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Response:
    def __init__(self, content):
        self.content = content

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self, *_):
        return self.content


def run_with_scripted_http(mod, sequence):
    opener = mock.Mock()
    opener.open.side_effect = sequence
    cookiejar = mock.MagicMock()
    cookiejar.__iter__.return_value = iter([object()])
    with mock.patch.object(mod.urllib.request, "build_opener", return_value=opener):
        with mock.patch.object(mod.http.cookiejar, "CookieJar", return_value=cookiejar):
            return mod.effective_admin_config("SuperSecret")


def test_login_http_error_reports_only_status_and_stage():
    mod = helper()
    err = HTTPError("http://private/secret", 403, "VERY_SECRET", {"Token": "leak"}, io.BytesIO(b"secret"))
    try:
        run_with_scripted_http(mod, [err])
        assert False, "Must fail closed"
    except mod.Blocked as exc:
        assert str(exc) == "FILESTASH_ADMIN_LOGIN_HTTP_403"
        for leak in ("private", "VERY_SECRET", "Token", "SuperSecret"):
            assert leak not in str(exc)


def test_config_http_error_reports_only_status_and_stage():
    mod = helper()
    err = HTTPError("http://private/config", 401, "SECRET", {}, io.BytesIO(b"secret"))
    try:
        run_with_scripted_http(mod, [Response(b'{"status":"ok"}'), err])
        assert False, "Must fail closed"
    except mod.Blocked as exc:
        assert str(exc) == "FILESTASH_ADMIN_CONFIG_HTTP_401"
        assert "SECRET" not in str(exc)


def test_login_network_failure_is_bounded():
    mod = helper()
    try:
        run_with_scripted_http(mod, [URLError("SECRET_SOCKET_EXCEPTION")])
        assert False, "Must fail closed"
    except mod.Blocked as exc:
        assert str(exc) == "FILESTASH_ADMIN_LOGIN_NETWORK_ERROR"
        assert "SECRET_SOCKET_EXCEPTION" not in str(exc)
