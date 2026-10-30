"""Filestash encrypts SMB middleware params at rest; never replace unknown data."""
import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
from unittest import mock

ROOT=Path(__file__).resolve().parents[1]
HELPER=ROOT/"roles/media_guest_setup/files/dh-filestash-smb-config.py"

def helper():
    spec=importlib.util.spec_from_file_location("dh_filestash_config",HELPER)
    m=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m

def native_form(values):
    """Construct the native Filestash admin schema without real secrets."""
    return {
        "middleware": {
            name: {field: {"label": field, "type": "text",
                           "value": value, "default": None}
                   for field, value in fields.items()}
            for name, fields in values["middleware"].items()
        }
    }


def seed(directory,opaque=False):
    module=helper()
    data=module.desired()
    data["general"]={"keep":"untouched","secret_key":"testsecretkey123"}
    if opaque:
        data["middleware"]["identity_provider"]["params"]="encrypted:ciphertext:1"
        data["middleware"]["attribute_mapping"]["params"]="encrypted:ciphertext:2"
    path=Path(directory)/"config.json"
    path.write_text(json.dumps(data))
    return module,path

def test_plaintext_json_semantic_match_requires_no_rewrite():
    with tempfile.TemporaryDirectory() as temp:
        mod,path=seed(temp)
        original=path.read_bytes()
        with mock.patch.object(mod, "effective_admin_config", side_effect=AssertionError("should not call auth")):
            mod.reconcile(path=path)
        assert path.read_bytes()==original

def test_runtime_decrypted_equivalence_preserves_encrypted_bytes_and_unrelated_state():
    with tempfile.TemporaryDirectory() as temp:
        mod,path=seed(temp,True)
        original=path.read_bytes()
        secret=Path(temp)/"secret.txt"
        with mock.patch.object(mod,"passport",return_value="sample88") as passport, \
             mock.patch.object(mod,"effective_admin_config",
                               return_value=native_form(mod.desired())) as api:
            mod.reconcile(path=path,credential=secret)
            passport.assert_called_once_with(secret)
            api.assert_called_once_with("sample88")
        assert path.read_bytes()==original
        assert json.loads(path.read_text())["general"]["keep"]=="untouched"

def test_encrypted_foreign_config_fails_closed_and_is_not_replaced():
    with tempfile.TemporaryDirectory() as temp:
        mod,path=seed(temp,True)
        original=path.read_bytes()
        foreign=native_form(mod.desired())
        foreign["middleware"]["identity_provider"]["type"]["value"]="foreign"
        with mock.patch.object(mod,"passport",return_value="sample88"), \
             mock.patch.object(mod,"effective_admin_config",return_value=foreign):
            try:
                mod.reconcile(path=path,credential=Path(temp)/"secret")
            except mod.Blocked as err:
                assert str(err)=="FILESTASH_RUNTIME_SMB_CONFIG_DRIFT"
            else:
                assert False,"Encrypted foreign config must not be overwritten"
        assert path.read_bytes()==original

def test_plaintext_middleware_param_json_key_order_is_semantically_equal():
    mod=helper()
    d=mod.desired()
    other=json.loads(json.dumps(d))
    params=json.loads(other["middleware"]["attribute_mapping"]["params"])
    other["middleware"]["attribute_mapping"]["params"]=json.dumps(params,sort_keys=True)
    assert mod.semantic_config(other,d)

def test_first_commission_preserves_unrelated_filestash_config():
    with tempfile.TemporaryDirectory() as temp:
        mod=helper()
        path=Path(temp)/"config.json"
        path.write_text(json.dumps({"general":{"keep":"owner-choice"}}))
        fake=SimpleNamespace(pw_uid=path.stat().st_uid,pw_gid=path.stat().st_gid)
        with mock.patch.object(mod.pwd,"getpwnam",return_value=fake):
            mod.reconcile(path=path)
        d=json.loads(path.read_text())
        assert mod.semantic_config(d,mod.desired())
        assert d["general"]["keep"]=="owner-choice"

def test_live_admin_api_login_is_loopback_only_without_outbound_proxy():
    mod=helper()
    requests=[]
    class Response:
        def __init__(self,payload):self.payload=json.dumps(payload).encode()
        def __enter__(self):return self
        def __exit__(self,*args):return False
        def read(self,n):return self.payload
    class FakeOpener:
        def open(self,request,timeout):
            url=request.full_url if hasattr(request,"full_url") else request
            requests.append((url,timeout))
            if url.endswith("/admin/api/session"):
                assert getattr(request,"data",None)
                return Response({"status":"ok","result":{"access_token":"fake-admin-access-token-12345"}})
            return Response({"status":"ok","result":native_form(mod.desired())})
    with mock.patch.object(mod.http.cookiejar,"CookieJar",return_value=["admin-session"]), \
         mock.patch.object(mod.urllib.request,"build_opener",return_value=FakeOpener()) as build:
        value=mod.effective_admin_config("test1234")
    assert mod.semantic_middleware(mod.native_admin_middleware(value),
                                   mod.desired()["middleware"])
    assert requests==[
        ("http://127.0.0.1:8334/admin/api/session",12),
        ("http://127.0.0.1:8334/admin/api/config",12)]
    assert build.call_args.args[0].proxies=={}

def test_source_declares_password_path_without_inline_secret():
    source=(ROOT/"roles/media_guest_setup/tasks/apps.yml").read_text()
    assert "--credential-file" in source
    assert "dh-filestash-smb-config" in source


def test_native_admin_form_projection_rejects_missing_or_foreign_fields():
    mod=helper()
    form=native_form(mod.desired())
    assert mod.native_admin_middleware(form)==mod.desired()["middleware"]
    invalid=json.loads(json.dumps(form))
    del invalid["middleware"]["attribute_mapping"]["params"]["value"]
    with mock.patch.object(mod,"passport",return_value="sample88"):
        try:
            mod.native_admin_middleware(invalid)
        except mod.Blocked as err:
            assert str(err)=="FILESTASH_RUNTIME_CONFIG_INVALID"
        else:
            assert False,"Missing runtime values must fail closed"
    invalid=json.loads(json.dumps(form))
    invalid["middleware"]["attribute_mapping"]["other"]={"value":"unexpected"}
    try:
        mod.native_admin_middleware(invalid)
    except mod.Blocked as err:
        assert str(err)=="FILESTASH_RUNTIME_SMB_CONFIG_DRIFT"
    else:
        assert False,"Extra runtime fields must fail closed"


def test_foreign_disk_connections_block_before_admin_api(tmp_path):
    mod=helper()
    config=mod.desired()
    config["connections"]=[{"label":"Foreign share","type":"samba"}]
    path=tmp_path/"config.json"
    path.write_text(json.dumps(config))
    original=path.read_bytes()
    with mock.patch.object(mod,"effective_admin_config",
                           side_effect=AssertionError("must not authenticate")):
        try:
            mod.reconcile(path=path,credential=tmp_path/"unused")
        except mod.Blocked as err:
            assert str(err)=="FILESTASH_EXISTING_CONNECTIONS_CONFLICT"
        else:
            assert False,"Foreign connection must be preserved"
    assert path.read_bytes()==original


def test_firstboot_seeds_stable_crypto_key_before_filestash_saves_middleware():
    with tempfile.TemporaryDirectory() as temp:
        mod=helper()
        path=Path(temp)/"config.json"
        path.write_text(json.dumps({"general":{"keep":"owner-choice"}}))
        fake=SimpleNamespace(pw_uid=path.stat().st_uid,pw_gid=path.stat().st_gid)
        with mock.patch.object(mod.pwd,"getpwnam",return_value=fake):
            mod.reconcile(path=path)
            first=json.loads(path.read_text())
            key=first["general"]["secret_key"]
            assert len(key)==16 and key.isalnum()
            assert mod.semantic_config(first,mod.desired())
            mod.reconcile(path=path)
            assert json.loads(path.read_text())["general"]["secret_key"]==key
        assert first["general"]["keep"]=="owner-choice"


def test_repair_exact_broken_idp_preserves_unrelated_config_and_root_only_backup():
    with tempfile.TemporaryDirectory() as temp:
        mod,path=seed(temp,True)
        original=json.loads(path.read_text())
        original["middleware"]["identity_provider"]["params"]="v"+("A"*80)
        original["middleware"]["attribute_mapping"]["params"]="v"+("B"*80)
        original["general"]["custom"]="owner-data"
        path.write_text(json.dumps(original))
        before=path.read_bytes()
        current_uid=path.stat().st_uid
        fake=SimpleNamespace(pw_uid=current_uid,pw_gid=path.stat().st_gid)
        with mock.patch.object(mod,"broken_idp_entrypoint",return_value=True), \
             mock.patch.object(mod.pwd,"getpwnam",return_value=fake), \
             mock.patch.object(mod,"BACKUP_ROOT",Path(temp)/"backups"), \
             mock.patch.object(mod,"effective_admin_config",side_effect=AssertionError("repair should not login")):
            mod.reconcile(path=path,credential=Path(temp)/"unused",
                          repair_broken_idp=True)
        after=json.loads(path.read_text())
        assert mod.semantic_config(after,mod.desired())
        assert after["general"]==original["general"]
        backups=list((Path(temp)/"backups").glob(".dh-filestash-idp-backup-*.json"))
        assert len(backups)==1
        assert backups[0].read_bytes()==before
        assert backups[0].stat().st_mode & 0o777==0o600


def test_repair_refuses_foreign_idp_even_when_error_is_present():
    with tempfile.TemporaryDirectory() as temp:
        mod,path=seed(temp,True)
        current=json.loads(path.read_text())
        current["middleware"]["identity_provider"]["type"]="openid"
        path.write_text(json.dumps(current))
        before=path.read_bytes()
        with mock.patch.object(mod,"broken_idp_entrypoint",return_value=True):
            try:
                mod.reconcile(path=path,repair_broken_idp=True)
            except mod.Blocked as exc:
                assert str(exc)=="FILESTASH_RECOVERY_FOREIGN_MIDDLEWARE"
            else:
                assert False,"Foreign IdP must not be rewritten"
        assert path.read_bytes()==before
        assert list(Path(temp).glob(".dh-filestash-idp-backup-*.json"))==[]


def test_repair_mode_preserves_healthy_encrypted_middleware():
    with tempfile.TemporaryDirectory() as temp:
        mod,path=seed(temp,True)
        initial=path.read_bytes()
        with mock.patch.object(mod,"broken_idp_entrypoint",return_value=False), \
             mock.patch.object(mod,"passport",return_value="sample88"), \
             mock.patch.object(mod,"effective_admin_config",
                               return_value=native_form(mod.desired())):
            mod.reconcile(path=path,credential=Path(temp)/"unused",
                          repair_broken_idp=True)
        assert path.read_bytes()==initial
        assert list(Path(temp).glob(".dh-filestash-idp-backup-*.json"))==[]


def test_task80_checks_real_filestash_login_beyond_http_root():
    import yaml
    tasks=yaml.safe_load((ROOT/"roles/media_guest_setup/tasks/apps.yml").read_text())
    by={t["name"]:t for t in tasks}
    assert "--repair-broken-idp" in by[
        "Reconcile Filestash SMB passthrough without creating web users"
    ]["ansible.builtin.command"]["argv"]
    auth=by["Verify actual Filestash SMB username and password entrypoint"]
    assert auth["ansible.builtin.uri"]["follow_redirects"]=="none"
    assert auth["ansible.builtin.uri"]["status_code"]==200
    assert "FILESTASH_SMB_IDP_LOGIN_BROKEN" in str(tasks)
