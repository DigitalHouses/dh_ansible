"""Read-only Cloudflare permission probe; no live API calls in CI."""
import importlib.util
import io
import json
from pathlib import Path
from urllib.error import HTTPError
import yaml

ROOT = Path(__file__).resolve().parents[1]
FILE = ROOT / "scripts/cloudflare-read-scope-check.py"
spec = importlib.util.spec_from_file_location("dh_cloudflare_scope", FILE)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
TOKEN = "t" * 44
ACCOUNT = "a" * 32


class Response:
    status = 200
    def __init__(self, value):
        self.buf = json.dumps({"success": True, "result": value}).encode()
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return None
    def read(self, n):
        return self.buf[:n]


class FakeAPI:
    def __init__(self, deny=None, tunnels=None):
        self.deny = deny
        self.calls = []
        self.tunnels = ([{"name": "serzhan-home", "account_tag": ACCOUNT}]
                        if tunnels is None else tunnels)
    def open(self, req, timeout):
        assert req.get_method() == "GET"
        assert req.full_url.startswith("https://api.cloudflare.com/client/v4/")
        assert TOKEN not in req.full_url
        assert req.get_header("Authorization") == "Bearer " + TOKEN
        assert timeout <= 10
        self.calls.append(req.full_url)
        if self.deny and self.deny in req.full_url:
            raise HTTPError(req.full_url, 403, "Forbidden", None, None)
        if "/zones?" in req.full_url:
            return Response([{"name": "digitalhouses.vip",
                              "account": {"id": ACCOUNT}}])
        if "/cfd_tunnel?" in req.full_url:
            return Response(self.tunnels)
        if "/access/apps?" in req.full_url:
            return Response([{"domain": "*.digitalhouses.vip"}])
        raise AssertionError(req.full_url)


def test_read_scope_success_not_write():
    api = FakeAPI()
    x = mod.audit(TOKEN, "serzhan-home", api)
    assert x["overall"] == "READ_CONFIRMED"
    assert x["zone_read"] == x["tunnel_read"] == x["access_read"] == "READ_CONFIRMED"
    assert x["tunnel_present"] == "YES"
    assert x["access_app_present"] == "YES"
    assert x["write"] == "NOT_VERIFIED"
    assert len(api.calls) == 3
    assert TOKEN not in json.dumps(x)


def test_no_target_tunnel_is_not_permission_denied():
    x = mod.audit(TOKEN, "serzhan-home", FakeAPI(tunnels=[]))
    assert x["tunnel_present"] == "NO"
    assert x["overall"] == "READ_CONFIRMED"


def test_missing_zone_read_is_inconclusive_not_token_failure():
    api = FakeAPI(deny="/zones?")
    x = mod.audit(TOKEN, "serzhan-home", api)
    assert x["overall"] == "INCONCLUSIVE"
    assert x["zone_read"] == "READ_DENIED"
    assert len(api.calls) == 1


def test_access_denied_is_partial_read_not_token_failure():
    x = mod.audit(TOKEN, "serzhan-home", FakeAPI(deny="/access/apps?"))
    assert x["overall"] == "PARTIAL_READ"
    assert x["access_read"] == "READ_DENIED"


def test_task06_scope_is_read_only_optional_and_secret_safe():
    plays = yaml.safe_load((ROOT/"playbooks/check-site-credentials.yml").read_text())
    play = plays[0]
    probe = next(t for t in play["tasks"] if t["name"].startswith("Check Cloudflare account"))
    assert probe["no_log"] is True
    assert probe["changed_when"] is False
    assert probe["ansible.builtin.command"]["argv"][0] == "python3"
    assert probe["ansible.builtin.command"]["stdin_add_newline"] is False
    assert "DH_CLOUDFLARE_API_TOKEN" not in str(probe["ansible.builtin.command"]["argv"])
    report = plays[-1]["tasks"][-2]["ansible.builtin.debug"]["msg"]
    assert "dh_cf_scope" in report["cloudflare_scope"]
    assert "dh_cf_scope" not in report["overall"]
    assert report["cloudflare_token_scope"] == "NOT_VERIFIED"


def test_bad_token_or_site_cannot_connect():
    class NoConnect:
        def open(self,*args,**kw):
            raise AssertionError("unexpected network connection")
    assert mod.audit("short", "serzhan-home", NoConnect())["zone_read"] == "INVALID_INPUT"
    assert mod.audit(TOKEN, "../bad", NoConnect())["zone_read"] == "INVALID_INPUT"
