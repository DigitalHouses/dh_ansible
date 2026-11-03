"""Contract tests for owner-shared media logins, no separate app passwords."""
import importlib.util
import os
from pathlib import Path
import tempfile
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


def load(source, name):
    spec = importlib.util.spec_from_file_location(name, ROOT / source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_sambee_uses_owner_password_for_first_start_and_remembers_it():
    sambee = load("roles/media_guest_setup/files/dh-sambee-owner.py", "sambee")
    with tempfile.TemporaryDirectory() as scratch:
        root = Path(scratch) / "digitalhouses" / "credentials"
        root.mkdir(parents=True)
        owner = root / "serzhan.txt"
        owner.write_text("username=serzhan\npassword=EightAA1\n")
        owner.chmod(0o640)
        state = {"owner_password": None, "initial_password": "RANDOM_INITIAL"}
        actions = []

        def token(username, password):
            if username == "serzhan" and password in (
                    state["owner_password"], state["initial_password"]):
                return "EXAMPLE_TOKEN"
            return None

        def change(path, body, bearer=None):
            assert path == "/api/auth/change-password"
            assert bearer == "EXAMPLE_TOKEN"
            assert body["current_password"] == state["initial_password"]
            state["owner_password"] = body["new_password"]
            state["initial_password"] = None
            actions.append("changed")

        with mock.patch.object(sambee.os, "geteuid", return_value=0), \
             mock.patch.object(sambee, "token", side_effect=token), \
             mock.patch.object(sambee, "first_credentials",
                               return_value=("serzhan", "RANDOM_INITIAL")), \
             mock.patch.object(sambee, "request", side_effect=change):
            sambee.reconcile(owner, Path(scratch) / "docker-compose.yml")
            sambee.reconcile(owner, Path(scratch) / "docker-compose.yml")
        assert actions == ["changed"]
        assert (root / ".sambee-owner.sha256").stat().st_mode & 0o777 == 0o600


def test_sambee_blocks_on_preexisting_different_owner_not_owned():
    sambee = load("roles/media_guest_setup/files/dh-sambee-owner.py", "sambee_block")
    with tempfile.TemporaryDirectory() as scratch:
        root = Path(scratch) / "digitalhouses" / "credentials"
        root.mkdir(parents=True)
        owner = root / "serzhan.txt"
        owner.write_text("username=serzhan\npassword=EightAA1\n")
        owner.chmod(0o640)
        with mock.patch.object(sambee.os, "geteuid", return_value=0), \
             mock.patch.object(sambee, "token", return_value=None), \
             mock.patch.object(sambee, "first_credentials",
                               return_value=("different_admin", "INITIAL")):
            try:
                sambee.reconcile(owner, Path(scratch) / "docker-compose.yml")
            except RuntimeError as error:
                assert str(error) == "SAM_BEE_INITIAL_USER_MISMATCH"
            else:
                assert False, "Foreign Sambee identity must block"


def test_arr_forms_login_is_created_only_once_using_owner_password():
    arr = load("roles/media_guest_setup/files/dh-media-arr-auth.py", "arr")
    with tempfile.TemporaryDirectory() as scratch:
        root = Path(scratch) / "digitalhouses" / "credentials"
        root.mkdir(parents=True)
        owner = root / "serzhan.txt"
        owner.write_text("username=serzhan\npassword=EightAA1\n")
        owner.chmod(0o640)
        configurations = {
            app: {"id": 1, "authenticationMethod": "none",
                  "authenticationRequired": "enabled", "username": ""}
            for app in arr.APPS
        }
        changes = []

        def api(app, key, method="GET", values=None, rid=None):
            assert key == "TEST_API"
            if method == "PUT":
                assert rid == 1 and values["password"] == "EightAA1"
                assert values["passwordConfirmation"] == "EightAA1"
                assert values["authenticationMethod"] == "forms"
                configurations[app] = dict(values)
                changes.append(app)
            return dict(configurations[app])

        with mock.patch.object(arr.os, "geteuid", return_value=0), \
             mock.patch.object(arr, "api_key", return_value="TEST_API"), \
             mock.patch.object(arr, "request", side_effect=api):
            arr.main(owner)
            arr.main(owner)
        assert changes == ["radarr", "sonarr", "prowlarr"]
        for app in changes:
            assert (root / (".arr-" + app + "-owner.sha256")).exists()


def test_arr_does_not_overwrite_a_preconfigured_foreign_login():
    arr = load("roles/media_guest_setup/files/dh-media-arr-auth.py", "arr_block")
    with tempfile.TemporaryDirectory() as scratch:
        root = Path(scratch) / "digitalhouses" / "credentials"
        root.mkdir(parents=True)
        owner = root / "serzhan.txt"
        owner.write_text("username=serzhan\npassword=EightAA1\n")
        owner.chmod(0o640)
        with mock.patch.object(arr.os, "geteuid", return_value=0), \
             mock.patch.object(arr, "api_key", return_value="TEST_API"), \
             mock.patch.object(arr, "request", return_value={
                 "id": 1, "authenticationMethod": "forms",
                 "authenticationRequired": "enabled", "username": "other"}):
            try:
                arr.main(owner)
            except RuntimeError as err:
                assert str(err) == "ARR_PREEXISTING_LOGIN_UNMANAGED"
            else:
                assert False, "Must never overwrite arbitrary existing Arr login"


def test_shared_login_length_policy_for_optional_customer_change():
    apps = load("roles/media_guest_setup/files/dh-media-app-accounts.py", "lenpolicy")
    with tempfile.TemporaryDirectory() as scratch:
        root = Path(scratch) / "demo-home-pool" / "digitalhouses"
        cred = root / "credentials"
        cred.mkdir(parents=True)
        f = cred / "demo.txt"
        f.chmod(0o640) if f.exists() else None
        with mock.patch.object(apps.grp, "getgrnam", return_value=type(
                "G", (), {"gr_gid": os.getgid()})()):
            for length in (6, 7, 8):
                f.write_text("username=demo\npassword=" + "B" * length + "\n")
                f.chmod(0o640)
                assert apps.read_owner(root, "demo") == "B" * length
            for length in (1, 5, 9):
                f.write_text("username=demo\npassword=" + "B" * length + "\n")
                f.chmod(0o640)
                try:
                    apps.read_owner(root, "demo")
                except RuntimeError as err:
                    assert str(err) == "OWNER_PASSWORD_POLICY_INVALID"
                else:
                    assert False, "Password outside supported range must be rejected"
