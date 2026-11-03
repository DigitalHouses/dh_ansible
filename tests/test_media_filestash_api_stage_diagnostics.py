"""Filestash admin API diagnostics must be stage-specific and secret-free."""
import importlib.util
import io
import http.cookiejar
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
        run_with_scripted_http(mod, [
            Response(b'{"status":"ok","result":{"access_token":"TEST_VALID_TOKEN_1234"}}'),
            err])
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


def test_native_bearer_token_authenticates_config_read_without_using_password_header():
    mod = helper()
    fake_access_token = "TEST_VALID_TOKEN_1234"
    opener = mock.Mock()
    opener.open.side_effect = [
        Response(b'{"status":"ok","result":{"access_token":"TEST_VALID_TOKEN_1234"}}'),
        Response(b'{"status":"ok","result":{"connections":[],"middleware":{}}}'),
    ]
    with mock.patch.object(mod.urllib.request, "build_opener", return_value=opener):
        with mock.patch.object(mod.http.cookiejar, "CookieJar", return_value=http.cookiejar.CookieJar()):
            result = mod.effective_admin_config("FAKE_LOGIN_ONLY")
    assert result == {"connections": [], "middleware": {}}
    assert opener.open.call_count == 2
    first = opener.open.call_args_list[0].args[0]
    second = opener.open.call_args_list[1].args[0]
    assert first.get_method() == "POST"
    assert first.get_header("Accept") == "application/json"
    assert first.get_header("X-requested-with") == "XmlHttpRequest"
    assert second.get_method() == "GET"
    assert second.get_header("X-requested-with") == "XmlHttpRequest"
    assert second.get_header("Authorization") == "Bearer " + fake_access_token
    assert "FAKE_LOGIN_ONLY" not in str(second.headers)


def test_missing_json_session_token_fails_closed_without_requesting_config():
    mod = helper()
    opener = mock.Mock()
    opener.open.side_effect = [Response(b'{"status":"ok","result":true}')]
    with mock.patch.object(mod.urllib.request, "build_opener", return_value=opener):
        with mock.patch.object(mod.http.cookiejar, "CookieJar", return_value=http.cookiejar.CookieJar()):
            try:
                mod.effective_admin_config("FAKE_LOGIN_ONLY")
                assert False, "Missing access token must fail closed"
            except mod.Blocked as exc:
                assert str(exc) == "FILESTASH_ADMIN_ACCESS_TOKEN_UNAVAILABLE"
    assert opener.open.call_count == 1
