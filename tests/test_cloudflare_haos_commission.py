"""Task 70 commissioning on an occupied runner must not touch task 10."""
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace
import sys


ROOT = Path(__file__).resolve().parents[1]


def load():
    file = ROOT / "bootstrap/cloudflare-commission.py"
    spec = importlib.util.spec_from_file_location("cf_haos_commission", file)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_haos_only_commission_preserves_existing_cutover(monkeypatch, capsys):
    mod = load()
    actions = []

    monkeypatch.setattr(sys, "argv", ["commission", "--site", "demo-home",
                                     "--ctid", "1231", "--haos-only"])
    monkeypatch.setattr(mod.os, "geteuid", lambda: 0)
    original_is_file = Path.is_file
    monkeypatch.setattr(Path, "is_file", lambda p: (
        True if str(p) == "/etc/pve/user.cfg" else original_is_file(p)))
    monkeypatch.setattr(mod, "valid_source",
                        lambda path: actions.append(("validate", path.name)))
    monkeypatch.setattr(mod, "stage_host",
                        lambda source, target, mode:
                        actions.append(("stage_host", source.name, str(target), mode)))
    monkeypatch.setattr(mod, "stage_guest",
                        lambda ctid, source, target, mode:
                        actions.append(("stage_guest", source.name, target, mode)))

    def checked(*argv):
        actions.append(("checked",) + argv)
        if argv == ("pct", "status", "1231"):
            return "status: running"
        if argv == ("pct", "exec", "1231", "--", "hostname"):
            return "ansible-demo-home"
        if argv == ("pct", "exec", "1231", "--", "systemctl",
                    "is-active", "dh-haos-mtls.path"):
            return "active"
        return ""

    monkeypatch.setattr(mod, "checked", checked)
    monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k:
                        SimpleNamespace(returncode=1))
    mod.main()
    assert "HAOS_MTLS_RUNNER_WATCHER=ACTIVE" in capsys.readouterr().out
    assert ("stage_host", "dh-cloudflare-pve-haos-mtls.py",
            "/usr/local/sbin/dh-cloudflare-pve-haos-mtls", 0o700) in actions
    assert ("stage_guest", "dh-haos-mtls-runner.py",
            "/usr/local/sbin/dh-haos-mtls-runner", 0o700) in actions
    assert not any("dh-cloudflare-pve-cutover" in str(a) or
                   "dh-cloudflare-runner-cutover" in str(a) or
                   "dh-cloudflare-switch.path" in str(a) for a in actions)


def test_pending_haos_mtls_request_blocks_before_commission(monkeypatch):
    import pytest
    mod = load()
    monkeypatch.setattr(sys, "argv", ["commission", "--site", "demo-home",
                                     "--ctid", "1231", "--haos-only"])
    monkeypatch.setattr(mod.os, "geteuid", lambda: 0)
    original_is_file = Path.is_file
    monkeypatch.setattr(Path, "is_file", lambda p: (
        True if str(p) == "/etc/pve/user.cfg" else original_is_file(p)))

    def checked(*argv):
        if argv == ("pct", "status", "1231"):
            return "status: running"
        if argv == ("pct", "exec", "1231", "--", "hostname"):
            return "ansible-demo-home"
        raise AssertionError("must block before other commands")

    monkeypatch.setattr(mod, "checked", checked)
    monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k:
                        SimpleNamespace(returncode=0))
    with pytest.raises(mod.Block, match="PENDING_HAOS_MTLS_REQUEST"):
        mod.main()
