"""Root PVE access commissioning must not recreate a running runner."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "bootstrap/semaphore-host-install.py"


def load():
    spec = importlib.util.spec_from_file_location("commissioner", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def args():
    return SimpleNamespace(site="demo-home", runner_cidr="192.0.2.231/24",
                           gateway="192.0.2.1", pve_ip="192.0.2.30")


def test_preflight_checks_occupied_runner_without_pve_writes(monkeypatch, capsys):
    m = load()
    monkeypatch.setattr(m.os, "geteuid", lambda: 0)
    existing_is_file = Path.is_file
    monkeypatch.setattr(Path, "is_file", lambda path: str(path) == "/etc/pve/user.cfg" or existing_is_file(path))
    calls = []
    def run(*a, **kw):
        calls.append(a)
        if a[:4] == ("ip", "-4", "-o", "addr"):
            return "2: vmbr0    inet 192.0.2.30/24"
        if a[:2] == ("pct", "config"):
            return ("hostname: ansible-demo-home\nunprivileged: 1\n"
                    "net0: name=eth0,bridge=vmbr0,gw=192.0.2.1,ip=192.0.2.231/24")
        if a[:2] == ("pct", "status"):
            return "status: running"
        raise AssertionError("Unexpected PVE command " + str(a))
    def guest(*a, **kw):
        if a == ("hostname",):
            return "ansible-demo-home"
        if a[1:] == ("-c", "import ansible;print(ansible.__version__)"):
            return "2.21.4"
        if a[:2] == ("systemctl", "is-active"):
            return "active"
        raise AssertionError("Unexpected guest read " + str(a))
    monkeypatch.setattr(m, "run", run)
    monkeypatch.setattr(m, "pct_guest", guest)
    monkeypatch.setattr(m, "code", lambda *a, **kw: 0 if a[-1] == "/opt/ansible/venv/bin/python" else 1)
    assert m.commission_pve_preflight(args()) == "/opt/ansible/venv/bin/python"
    assert "PVE_COMMISSION_PREFLIGHT=PASS" in capsys.readouterr().out
    assert all(a[1] not in ("create", "destroy", "user", "push") for a in calls)


def test_commissioning_requires_exact_site_cidr(monkeypatch):
    m = load()
    monkeypatch.setattr(m.os, "geteuid", lambda: 0)
    monkeypatch.setattr(Path, "is_file", lambda _: True)
    bad = args()
    bad.runner_cidr = "192.0.2.225/24"
    with pytest.raises(m.Blocked, match="Site runner and PVE address mismatch"):
        m.commission_pve_preflight(bad)


def test_cli_preflight_never_runs_host_install_or_enrollment(monkeypatch):
    m = load()
    calls = []
    monkeypatch.setattr(m, "commission_pve_preflight", lambda args: calls.append("preflight"))
    monkeypatch.setattr(m, "commission_existing_pve_access", lambda args: calls.append("commission"))
    monkeypatch.setattr(m, "host_preflight", lambda *a, **kw: calls.append("guest_create"))
    outcome = m.main(["--site", "demo-home", "--runner-cidr", "192.0.2.231/24",
                      "--gateway", "192.0.2.1", "--pve-ip", "192.0.2.30",
                      "--commission-pve-access"])
    assert outcome == 0
    assert calls == ["preflight"]


def test_pve_commissioning_requires_pinned_fleet_and_confirmation():
    src = SCRIPT.read_text()
    assert "assert_private_fleet_head(args.expected_fleet_commit, env)" in src
    assert 'exact = "COMMISSION PVE ACCESS " + args.site' in src
    assert 'commission_pve_preflight(args, checkout)' in src
    assert "guest_python=guest_python" in src
    assert "provision_pve_identity_stage(args.site" in src
    assert "create_runner(" not in src[src.index("def commission_existing_pve_access("):
                                    src.index("def main(argv=None):")]
