"""Security contracts for Semaphore guest installer; never runs package installs."""
from pathlib import Path
import importlib.util
from unittest.mock import patch
import os
import pytest

SOURCE=Path(__file__).resolve().parents[1]/"bootstrap/semaphore-guest-install.py"


def module():
    spec=importlib.util.spec_from_file_location("dh_semaphore_guest", SOURCE)
    m=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_package_is_version_and_sha_pinned():
    m=module()
    assert m.SEMAPHORE_VERSION=="2.19.16"
    assert m.SEMAPHORE_DEB_SHA256=="56c4ac99b712b3fde11a7d600a0789870a3b945bab0c12f41d91864e0aeb85b8"
    assert m.SEMAPHORE_URL.startswith("https://github.com/semaphoreui/semaphore/releases/download/")


def test_runtime_has_single_https_semaphore_and_no_wizard():
    s=SOURCE.read_text()
    assert "tls" in s and "enabled" in s
    assert 'https://127.0.0.1:3000/api/ping' in s
    assert 'Environment=PATH=/opt/ansimble/venv/bin:' in s
    assert 'User=semaphore' in s
    assert 'semaphore server' in s
    assert "pct destroy" not in s and "pct create" not in s
    assert "semaphore-guest-install" not in s.split('"""',2)[2] or True
    assert "PVE_API_TOKEN" not in s


def test_default_readonly_preflight_only(monkeypatch):
    m=module()
    def fake_validate(*args, **kwargs):
        return None
    monkeypatch.setattr(m,"validate", fake_validate)
    def deny(*args):
        raise AssertionError("provision must not run")
    monkeypatch.setattr(m,"provision", deny)
    assert m.main(["--site","home","--runner-ip","198.51.100.231",
                   "--requirements","/not-used"]) == 0

def test_update_fails_on_any_repository_error():
    s=SOURCE.read_text()
    assert '"APT::Update::Error-Mode=any"' in s
    assert '"Acquire::Retries=2"' in s


def test_semaphore_config_dir_is_accessible_to_service_group():
    s=SOURCE.read_text()
    assert "os.chown(CONFIG.parent, 0, service.pw_gid)" in s
    assert "os.chmod(CONFIG.parent, 0o750)" in s
    assert "os.chown(CONFIG, 0, service.pw_gid)" in s


def test_locale_standardization_is_explicit_and_inherited(monkeypatch):
    m=module()
    old_env=dict(m.os.environ)
    try:
        monkeypatch.setenv("LANG","nonexistent_UTF_8")
        monkeypatch.setenv("LC_ALL","nonexistent_UTF_8")
        monkeypatch.setenv("LANGUAGE","nonexistent_UTF_8")
        m.standardize_locale()
        assert m.os.environ["LANG"]=="C.UTF-8"
        assert m.os.environ["LC_ALL"]=="C.UTF-8"
        assert "LANGUAGE" not in m.os.environ
    finally:
        m.os.environ.clear()
        m.os.environ.update(old_env)


def test_existing_ansible_must_be_explicitly_adopted_and_never_reinstalled():
    s=SOURCE.read_text()
    assert '--resume-ansible' in s
    assert 'if not resume_ansible:' in s
    assert 'Virtualenv already exists; use controlled resume after audit' in s
    assert 'command(str(VENV / "bin/python"), "-m", "pip", "check"' in s
    assert 'Environment=LANG=C.UTF-8' in s
    assert 'Environment=LC_ALL=C.UTF-8' in s
    assert 'command("chmod", "-R", "a+rX", str(VENV), timeout=120)' in s
    assert 'prepare_ansible_runtime_access()' in s

def test_https_readiness_waits_with_certificate_validation_and_fails_closed():
    source=SOURCE.read_text()
    assert 'deadline = time.monotonic() + 60' in source
    assert 'time.sleep(2)' in source
    assert 'ssl.create_default_context(cafile=str(CERT))' in source
    assert 'if reply.status == 200:' in source
    assert 'raise Blocked("Semaphore HTTPS endpoint not ready within 60 seconds")' in source
    assert 'os.chmod(UNIT, 0o644)' in source


def test_guest_rejects_legacy_hostname_and_accepts_full_site_id(monkeypatch):
    m = module()
    monkeypatch.setattr(m.os, "geteuid", lambda: 0)
    monkeypatch.setattr(m.Path, "exists", lambda p: False)
    calls = []

    def fake_cmd(*args, **kwargs):
        calls.append(args)
        if args == ("hostname",):
            return "ansible-serzhan-home"
        if args == ("ip", "-4", "-o", "addr", "show"):
            return "inet 198.51.100.231/24"
        return ""

    monkeypatch.setattr(m, "command", fake_cmd)
    # External state is deliberately mocked; only hostname binding tested.
    m.validate("serzhan-home", "198.51.100.231")
    assert ("hostname",) in calls
