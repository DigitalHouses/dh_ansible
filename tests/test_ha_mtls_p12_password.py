"""No secret export or mutation when showing an existing mobile P12 password."""
from __future__ import annotations
import importlib.util
from pathlib import Path
import io
import pytest

ROOT = Path(__file__).resolve().parents[1]
POLICY = {"hostname": "sample-home-ha-mtls.digitalhouses.vip",
          "generation_id": "a" * 32,
          "certificate_sha256": "b" * 64,
          "certificate_id": "11111111-1111-4111-8111-111111111111",
          "p12_filename": "sample-home-ha-mtls-aaaaaaaa.p12"}


def load():
    spec = importlib.util.spec_from_file_location(
        "p12_password_local", ROOT / "bin/ha-mtls-p12-password.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeVerifier:
    def __init__(self, password="synthetic-P12-password-42-characters-only"):
        self.password = password
    def recovery_identity(self, ciphertext, master, site, status):
        assert site == "sample-home"
        assert status == "issued_unpublished"
        return POLICY["hostname"], "unused", "unused", POLICY["generation_id"], {
            "cloudflare_certificate_id": POLICY["certificate_id"],
            "p12_password": self.password,
        }
    def verify_issued_identity(self, host, key, csr, entry, fingerprint):
        assert host == POLICY["hostname"] and fingerprint == POLICY["certificate_sha256"]
        return entry["cloudflare_certificate_id"]


def test_password_recovered_from_correct_issued_generation_without_output(capsys):
    m = load()
    value = m.check_material(FakeVerifier(), b"encrypted-only", b"synthetic", "sample-home", POLICY)
    assert len(value) >= 32
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("password", ("short", "x"*36+"\n", "a"*40+"\x00"))
def test_unsafe_password_is_rejected(password):
    m = load()
    with pytest.raises(m.Block, match="P12_IMPORT_PASSWORD_INVALID"):
        m.check_material(FakeVerifier(password), b"ciphertext", b"synthetic", "sample-home", POLICY)


def test_wrong_certificate_identity_is_rejected():
    m = load()
    class Wrong(FakeVerifier):
        def verify_issued_identity(self, *args):
            return "00000000-0000-0000-0000-000000000000"
    with pytest.raises(m.Block, match="P12_IMPORT_CERTIFICATE_ID_CHANGED"):
        m.check_material(Wrong(), b"ciphertext", b"synthetic", "sample-home", POLICY)


def test_only_tty_receive_secret_not_printed_or_saved(monkeypatch, capsys):
    m = load()
    written = []
    descriptors = []
    monkeypatch.setattr(m.os, "isatty", lambda fd: fd in (0, 1))
    monkeypatch.setattr(m.os, "open", lambda path, flags: 42)
    monkeypatch.setattr(m.os, "close", lambda fd: descriptors.append(fd))
    monkeypatch.setattr(m.os, "write", lambda fd, data: (
        written.append((fd, data)) or len(data)
    ))
    monkeypatch.setattr(m.sys, "stdin", io.StringIO("\n"))
    pwd = "synthetic-P12-password-42-characters-only"
    m.show_once_on_terminal(pwd, POLICY["p12_filename"])
    assert pwd.encode() in b"".join(data for _, data in written)
    assert written[-1] == (42, b"\x1b[2J\x1b[3J\x1b[H")
    assert descriptors == [42]
    output = capsys.readouterr()
    assert output.out == "" and output.err == ""


def test_pct_exec_controlling_tty_unavailable_but_real_stdout_tty(monkeypatch):
    m = load()
    written = []
    monkeypatch.setattr(m.os, "isatty", lambda fd: fd in (0, 1))
    def deny_dev_tty(path, flags):
        raise OSError("pct exec process has no controlling tty")
    monkeypatch.setattr(m.os, "open", deny_dev_tty)
    monkeypatch.setattr(m.os, "write", lambda fd, data: (
        written.append((fd, data)) or len(data)
    ))
    monkeypatch.setattr(m.sys, "stdin", io.StringIO("\n"))
    m.show_once_on_terminal("synthetic-P12-password-42-characters-only", POLICY["p12_filename"])
    assert written and all(fd == 1 for fd, _ in written)
    assert written[-1][1] == b"\x1b[2J\x1b[3J\x1b[H"


def test_fails_closed_when_dev_tty_unavailable_and_stdout_redirected(monkeypatch):
    m = load()
    monkeypatch.setattr(m.os, "isatty", lambda fd: fd == 0)
    monkeypatch.setattr(m.os, "open", lambda path, flags: (_ for _ in ()).throw(OSError("no tty")))
    with pytest.raises(m.Block, match="LOCAL_TTY_OUTPUT_UNAVAILABLE"):
        m.show_once_on_terminal("synthetic-P12-password-42-characters-only", POLICY["p12_filename"])


def test_noninteractive_context_does_not_show_password(monkeypatch):
    m = load()
    monkeypatch.setattr(m.os, "isatty", lambda fd: False)
    with pytest.raises(m.Block, match="INTERACTIVE_TERMINAL_REQUIRED"):
        m.show_once_on_terminal("synthetic-P12-password-42-characters-only", POLICY["p12_filename"])
