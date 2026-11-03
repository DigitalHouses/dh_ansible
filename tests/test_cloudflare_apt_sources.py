"""Reproduce real PVE Signed-By collision without touching a system APT config."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

HOST = Path(__file__).resolve().parents[1] / "bootstrap/cloudflare-pve-cutover.py"


def load():
    spec = importlib.util.spec_from_file_location("dh_cloudflare_apt", HOST)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fixture(monkeypatch, tmp_path):
    m = load()
    apt_dir = tmp_path / "sources.list.d"
    apt_dir.mkdir()
    monkeypatch.setattr(m, "CLOUDFLARED_APT_DIR", apt_dir)
    monkeypatch.setattr(m, "CLOUDFLARED_APT_MAIN", tmp_path / "sources.list")
    monkeypatch.setattr(m, "CLOUDFLARED_OWN_SOURCE", apt_dir / "dh-cloudflared.list")
    monkeypatch.setattr(m, "CLOUDFLARED_OWN_KEY", tmp_path / "dh-cloudflared.gpg")
    # System package source files are root-owned; make only our fixture files
    # report root ownership in an otherwise unprivileged CI job.
    original = m.Path.stat
    def stat_as_root(p, *args, **kwargs):
        value = original(p, *args, **kwargs)
        if p == apt_dir or apt_dir in p.parents:
            return SimpleNamespace(st_uid=0, st_mode=value.st_mode,
                                   st_size=value.st_size)
        return value
    monkeypatch.setattr(m.Path, "stat", stat_as_root)
    return m, apt_dir


def test_reuse_existing_vendor_source_and_remove_own_duplicate(monkeypatch, tmp_path):
    m, apt_dir = fixture(monkeypatch, tmp_path)
    vendor = apt_dir / "cloudflared.list"
    vendor_text = ("deb [signed-by=/usr/share/keyrings/cloudflare-public-v2.gpg] "
                   "https://pkg.cloudflare.com/cloudflared any main\n")
    vendor.write_text(vendor_text)
    managed = m.CLOUDFLARED_OWN_SOURCE
    managed.write_text(
        "deb [signed-by=/usr/share/keyrings/dh-cloudflared.gpg] "
        "https://pkg.cloudflare.com/cloudflared any main\n")
    keys = []
    monkeypatch.setattr(m, "validate_existing_apt_key", keys.append)
    binaries = iter((None, "/usr/bin/cloudflared"))
    monkeypatch.setattr(m.shutil, "which", lambda _: next(binaries))
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout="")
    monkeypatch.setattr(m.subprocess, "run", run)
    result = m.install_missing_cloudflared()
    assert result == "/usr/bin/cloudflared"
    assert keys == ["/usr/share/keyrings/cloudflare-public-v2.gpg"]
    assert vendor.read_text() == vendor_text
    assert not managed.exists()
    assert [c[0:2] for c in calls] == [
        ["/usr/bin/apt-get", "update"],
        ["/usr/bin/apt-get", "install"]]
    assert not any("gpg" in c[0] for c in calls)


def test_ambiguous_vendor_source_blocks_before_any_mutation(monkeypatch, tmp_path):
    m, apt_dir = fixture(monkeypatch, tmp_path)
    for name, key in [("a.list", "cloudflare-public-v2.gpg"),
                      ("b.list", "cloudflare-main.gpg")]:
        (apt_dir / name).write_text(
            f"deb [signed-by=/usr/share/keyrings/{key}] "
            "https://pkg.cloudflare.com/cloudflared any main\n")
    managed = m.CLOUDFLARED_OWN_SOURCE
    managed.write_text(
        "deb [signed-by=/usr/share/keyrings/dh-cloudflared.gpg] "
        "https://pkg.cloudflare.com/cloudflared any main\n")
    monkeypatch.setattr(m, "validate_existing_apt_key", lambda _: None)
    monkeypatch.setattr(m.shutil, "which", lambda _: None)
    with pytest.raises(m.Block, match="MULTIPLE_EXISTING_SOURCES"):
        m.install_missing_cloudflared()
    assert managed.exists()


def test_unknown_existing_vendor_source_blocks_before_changes(monkeypatch, tmp_path):
    m, apt_dir = fixture(monkeypatch, tmp_path)
    (apt_dir / "cloudflared.list").write_text(
        "deb [trusted=yes] https://pkg.cloudflare.com/cloudflared any main\n")
    monkeypatch.setattr(m.shutil, "which", lambda _: None)
    with pytest.raises(m.Block, match="SOURCE_UNKNOWN"):
        m.install_missing_cloudflared()


def test_detect_signed_deb822_vendor_source(monkeypatch, tmp_path):
    m, apt_dir = fixture(monkeypatch, tmp_path)
    (apt_dir / "cf.sources").write_text(
        "Types: deb\nURIs: https://pkg.cloudflare.com/cloudflared\n"
        "Suites: any\nComponents: main\n"
        "Signed-By: /usr/share/keyrings/cloudflare-main.gpg\n")
    keys = []
    monkeypatch.setattr(m, "validate_existing_apt_key", keys.append)
    assert m.find_existing_cloudflared_apt_source()
    assert keys == ["/usr/share/keyrings/cloudflare-main.gpg"]


def test_no_vendor_source_still_selects_own_source(monkeypatch, tmp_path):
    m, _ = fixture(monkeypatch, tmp_path)
    assert not m.find_existing_cloudflared_apt_source()
