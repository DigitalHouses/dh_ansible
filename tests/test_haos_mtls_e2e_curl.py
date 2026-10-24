"""Regression: the PVE Cloudflare mTLS smoke test must use curl, not urllib.

2026-10-24: a valid Cloudflare client certificate returned HTTP 401 through
curl, but urllib's SSLContext returned 403, falsely failing Semaphore task70
after the protected route was already provisioned.
"""
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
WORKER = ROOT / "bootstrap/dh-cloudflare-pve-haos-mtls.py"
HOST = "sample-home-pve-ha-mtls.digitalhouses.vip"
URL = "https://" + HOST + "/api/"


def load():
    spec = importlib.util.spec_from_file_location("mtls_e2e_curl_test", WORKER)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_https_status_uses_verified_curl_and_never_disables_tls(monkeypatch):
    m = load()
    seen = []

    def run(argv, **kwargs):
        seen.append((argv, kwargs))
        return SimpleNamespace(returncode=0, stdout=b"401")

    monkeypatch.setattr(m.subprocess, "run", run)
    assert m.curl_https_status(URL, key=Path("/material/client.key"),
                              cert=Path("/material/client.crt")) == 401
    argv, opts = seen[0]
    assert argv[0] == "/usr/bin/curl"
    assert argv[-1] == URL
    assert ("--cert", "/material/client.crt") == tuple(argv[argv.index("--cert"):argv.index("--cert")+2])
    assert ("--key", "/material/client.key") == tuple(argv[argv.index("--key"):argv.index("--key")+2])
    assert "--insecure" not in argv and "-k" not in argv
    assert argv[argv.index("--proto")+1] == "=https"
    assert argv[argv.index("--noproxy")+1] == "*"
    assert argv[argv.index("--output")+1] == "/dev/null"
    assert opts["stdin"] is m.subprocess.DEVNULL
    assert opts["stderr"] is m.subprocess.DEVNULL
    with pytest.raises(m.Block, match="E2E_KEY_CERT_PAIR_REQUIRED"):
        m.curl_https_status(URL, key=Path("/material/client.key"))
    with pytest.raises(m.Block, match="E2E_URL_NOT_HTTPS"):
        m.curl_https_status("http://example.test/api/")


def test_bad_curl_status_cannot_pass(monkeypatch):
    m = load()
    for result in (SimpleNamespace(returncode=7, stdout=b"000"),
                   SimpleNamespace(returncode=0, stdout=b"401\n"),
                   SimpleNamespace(returncode=0, stdout=b"200")):
        monkeypatch.setattr(m.subprocess, "run", lambda *a, **kw: result)
        assert m.curl_https_status(URL) == (200 if result.stdout == b"200" else None)


def test_public_check_bounded_retry_on_initial_cert_denial(monkeypatch):
    m = load()
    seen = []
    sleeps = []
    def status(url, key=None, cert=None):
        assert url == URL
        if key is None:
            seen.append("anon")
            return 403
        seen.append("cert")
        assert str(key) == "private.key" and str(cert) == "cert.pem"
        return 403 if seen.count("cert") == 1 else 401
    monkeypatch.setattr(m, "curl_https_status", status)
    monkeypatch.setattr(m.time, "sleep", lambda sec: sleeps.append(sec))
    assert m.public_check({"hostname": HOST}, Path("private.key"), Path("cert.pem")) is None
    assert seen == ["anon", "cert", "anon", "cert"]
    assert sleeps == [4]


def test_public_check_never_allows_unprotected_host(monkeypatch):
    m = load()
    seen = []
    def status(url, key=None, cert=None):
        seen.append(key is not None)
        return 401
    monkeypatch.setattr(m, "curl_https_status", status)
    with pytest.raises(m.Block, match="ANONYMOUS_NOT_BLOCKED"):
        m.public_check({"hostname": HOST}, Path("private.key"), Path("cert.pem"))
    assert seen == [False]


def test_public_check_fails_closed_after_eight_client_cert_403s(monkeypatch):
    m = load()
    calls = []
    monkeypatch.setattr(m, "curl_https_status", lambda url,key=None,cert=None:
                        calls.append(key is not None) or 403)
    monkeypatch.setattr(m.time, "sleep", lambda sec: None)
    with pytest.raises(m.Block, match="VALID_CERT_NOT_REACHING_HA"):
        m.public_check({"hostname": HOST}, Path("private.key"), Path("cert.pem"))
    assert len(calls) == 16


def test_public_check_fails_closed_when_dns_is_unavailable(monkeypatch):
    m = load()
    monkeypatch.setattr(m, "curl_https_status", lambda *a, **kw: None)
    monkeypatch.setattr(m.time, "sleep", lambda sec: None)
    with pytest.raises(m.Block, match="ANONYMOUS_NOT_BLOCKED"):
        m.public_check({"hostname": HOST}, Path("private.key"), Path("cert.pem"))
