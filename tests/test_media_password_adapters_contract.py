"""Synthetic native API tests for root-only media owner password adapters."""
from contextlib import redirect_stdout
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
from unittest import mock
import urllib.error

import yaml

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "roles/media_guest_setup/files/dh-media-password-adapters.py"


def module(path, name):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    obj = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obj)
    return obj


def adapter_module():
    return module("roles/media_guest_setup/files/dh-media-password-adapters.py",
                  "test_media_adapters")


def test_samba_passes_password_only_in_root_protected_auth_file_and_stdin():
    mod = adapter_module()
    observed = []
    def execute(argv, input=None, timeout=20):
        observed.append((list(argv), input))
        if argv[0].endswith("smbclient"):
            assert "original8" in Path(argv[2]).read_text()
            assert Path(argv[2]).stat().st_mode & 0o777 == 0o600
        return True
    with mock.patch.object(mod, "silent", side_effect=execute):
        samba = mod.Samba("serzhan")
        assert samba.verify("original8")
        samba.apply("original8", "newpass8")
    assert len(observed) == 3
    assert "newpass8" not in repr(observed[1][0]) + repr(observed[2][0])
    assert b"newpass8\nnewpass8\n" == observed[-1][1]


def test_jellyfin_changes_only_verified_logged_in_user():
    mod = adapter_module()
    calls = []
    class Fake:
        def request(self, method, path, payload=None, token=None):
            calls.append((method, path, payload, token))
            if path == "/Users/AuthenticateByName":
                return {"AccessToken": "OLD_AUTH_TOKEN",
                        "User": {"Id": "1" * 32}} if payload["Pw"] == "oldpass8" else None
            assert path == "/Users/" + "1" * 32 + "/Password"
            assert payload == {"CurrentPw": "oldpass8", "NewPw": "newpass8",
                               "ResetPassword": False}
            assert token == "OLD_AUTH_TOKEN"
            return {}
    with mock.patch.object(mod, "helper", return_value=Fake()):
        j = mod.Jellyfin("serzhan")
        assert j.verify("oldpass8")
        j.apply("oldpass8", "newpass8")
    assert len(calls) == 3


def test_sambee_uses_upstream_authenticated_change_password_api():
    mod = adapter_module()
    calls = []
    class Fake:
        def token(self, username, password):
            return "TOKEN" if username == "serzhan" and password == "oldpass8" else None
        def request(self, path, body, bearer=None):
            calls.append((path, body, bearer))
    with mock.patch.object(mod, "helper", return_value=Fake()):
        sambee = mod.Sambee("serzhan")
        assert sambee.verify("oldpass8")
        assert not sambee.verify("badpass8")
        sambee.apply("oldpass8", "newpass8")
    assert calls == [("/api/auth/change-password",
                      {"current_password": "oldpass8", "new_password": "newpass8"},
                      "TOKEN")]


def test_serrvarr_host_config_changes_only_password_with_forms_enabled():
    mod = adapter_module()
    writes = []
    class Fake:
        def api_key(self, app):
            return "API_LOCAL"
        def request(self, app, key, method="GET", values=None, rid=None):
            if method == "PUT":
                writes.append((app, key, method, values, rid))
            return {"id": 2, "authenticationMethod": "forms",
                    "authenticationRequired": "enabled", "username": "serzhan",
                    "port": 7878}
    with mock.patch.object(mod, "helper", return_value=Fake()):
        r = mod.Servarr("radarr", "serzhan")
        with mock.patch.object(r, "verify", return_value=True):
            r.apply("oldpass8", "newpass8")
    assert len(writes) == 1
    assert writes[0][3]["password"] == "newpass8"
    assert writes[0][3]["passwordConfirmation"] == "newpass8"
    assert writes[0][3]["port"] == 7878


def test_qbittorrent_rechecks_over_lan_ip_not_loopback():
    mod = adapter_module()
    seen = []
    class FakeResponse:
        status = 200
        def read(self, count):
            return b"Ok."
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
    class Fake:
        def call(self, path, value=None):
            seen.append((path, value))
            return {"web_ui_username": "serzhan",
                    "bypass_local_auth": True,
                    "bypass_auth_subnet_whitelist_enabled": False}
    with mock.patch.object(mod, "helper", return_value=Fake()):
        q = mod.QBittorrent("serzhan", "192.0.2.238")
        with mock.patch.object(mod.urllib.request, "build_opener") as maker:
            maker.return_value.open.return_value = FakeResponse()
            assert q.verify("oldpass8")
            request = maker.return_value.open.call_args.args[0]
            assert request.full_url == (
                "http://192.0.2.238:8080/api/v2/auth/login")
            assert "Origin" in request.headers
            assert isinstance(maker.call_args.args[0],
                              mod.urllib.request.ProxyHandler)
            assert maker.call_args.args[0].proxies == {}
        with mock.patch.object(q, "verify", return_value=True):
            q.apply("oldpass8", "newpass8")
    assert seen[-1][0] == "/api/v2/app/setPreferences"
    assert seen[-1][1]["web_ui_password"] == "newpass8"
    assert seen[-1][1]["bypass_auth_subnet_whitelist_enabled"] is False


def test_password_rotation_files_staged_and_normal_ansible_apply_guarded():
    main = yaml.safe_load((ROOT / "roles/media_guest_setup/tasks/main.yml").read_text())
    names = [x["name"] for x in main]
    assert names.index("Protect application logins from incomplete password change") < names.index(
        "Require Fleet identities and provision authenticated SMB access")
    assert "MEDIA_PASSWORD_ROTATION_PENDING_RECOVERY" in str(main)
    assert any(x.get("ansible.builtin.import_tasks") == "password_rotation.yml"
               for x in main)
    tasks = yaml.safe_load(
        (ROOT / "roles/media_guest_setup/tasks/password_rotation.yml").read_text())
    assert any("0700" in str(x) for x in tasks)
    assert any("0600" in str(x) for x in tasks)
    assert "web" not in " ".join(x["name"].lower() for x in tasks)
    setup = (ROOT / "bootstrap/semaphore-host-install.py").read_text()
    for name in ("dh-media-password-transaction.py",
                 "dh-media-password-adapters.py",
                 "dh-media-password-rotate.py",
                 "tasks/password_rotation.yml"):
        assert name in setup


def test_cli_manifest_requires_explicit_service_activation_flags():
    mod = module("roles/media_guest_setup/files/dh-media-password-rotate.py",
                 "rotation_cli")
    with tempfile.TemporaryDirectory() as d:
        base = Path(d)
        path = base / "manifest.json"
        data = {"schema": 1, "owner": "serzhan", "pool": "serzhan-pool",
                "guest_ipv4": "192.168.11.238",
                "apps": {x: True for x in mod.KNOWN}}
        path.write_text(json.dumps(data))
        path.chmod(0o600)
        with mock.patch.object(mod, "MANIFEST", path):
            assert mod.configuration(path)[0] == data
            data["apps"]["radarr"] = False
            path.write_text(json.dumps(data))
            try:
                mod.configuration(path)
            except RuntimeError as error:
                assert str(error) == "ROTATION_MANIFEST_INVALID"
            else:
                assert False, "Mandatory service cannot be silently omitted"
