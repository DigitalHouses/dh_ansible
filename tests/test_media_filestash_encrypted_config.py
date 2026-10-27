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

def seed(directory,opaque=False):
    module=helper()
    data=module.desired()
    data["general"]={"keep":"untouched"}
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
             mock.patch.object(mod,"effective_admin_config",return_value=mod.desired()) as api:
            mod.reconcile(path=path,credential=secret)
            passport.assert_called_once_with(secret)
            api.assert_called_once_with("sample88")
        assert path.read_bytes()==original
        assert json.loads(path.read_text())["general"]["keep"]=="untouched"

def test_encrypted_foreign_config_fails_closed_and_is_not_replaced():
    with tempfile.TemporaryDirectory() as temp:
        mod,path=seed(temp,True)
        original=path.read_bytes()
        with mock.patch.object(mod,"passport",return_value="sample88"), \
             mock.patch.object(mod,"effective_admin_config",return_value={
                 "connections": [{"label":"Other","type":"samba"}],
                 "middleware": mod.desired()["middleware"]}):
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
        fake=SimpleNamespace(pw_uid=0,pw_gid=0)
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
            return Response({"status":"ok","result":mod.desired()})
    with mock.patch.object(mod.http.cookiejar,"CookieJar",return_value=["admin-session"]), \
         mock.patch.object(mod.urllib.request,"build_opener",return_value=FakeOpener()) as build:
        value=mod.effective_admin_config("test1234")
    assert mod.semantic_config(value,mod.desired())
    assert requests==[
        ("http://127.0.0.1:8334/admin/api/session",12),
        ("http://127.0.0.1:8334/admin/api/config",12)]
    assert build.call_args.args[0].proxies=={}

def test_source_declares_password_path_without_inline_secret():
    source=(ROOT/"roles/media_guest_setup/tasks/apps.yml").read_text()
    assert "--credential-file" in source
    assert "dh-filestash-smb-config" in source
