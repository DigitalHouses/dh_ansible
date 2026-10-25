"""Offline functional tests for pinned private-site source verification."""
from pathlib import Path
import base64
import hashlib
import importlib.util

import pytest

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "bootstrap/dh-site-data-pve-refresh.py"


def module():
    spec = importlib.util.spec_from_file_location("pve_site_source", HELPER)
    item = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(item)
    return item


def test_pinned_site_component_sha_and_blob_are_independently_verified(monkeypatch):
    m = module()
    raw = b"---\nmedia_vm:\n  name: media-server-demo-home\n"
    blob = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
    pin = "a" * 40
    def fake_api(path, token):
        assert token == "synthetic-only"
        if path.endswith("heads/main"):
            return {"object": {"sha": pin}}
        assert path.endswith("/inventories/demo-home/media_vm.yml?ref=" + pin)
        return {"type": "file", "encoding": "base64", "size": len(raw),
                "content": base64.b64encode(raw).decode(), "sha": blob}
    monkeypatch.setattr(m, "api", fake_api)
    assert m.pinned_source("demo-home", "media_vm", "synthetic-only") == (raw, pin)


def test_private_source_modified_bytes_fail_blob_integrity(monkeypatch):
    m = module()
    raw = b"---\nmedia_vm:\n  vmid: 2238\n"
    def fake_api(path, token):
        if path.endswith("heads/main"):
            return {"object": {"sha": "b" * 40}}
        return {"type": "file", "encoding": "base64", "size": len(raw),
                "content": base64.b64encode(raw).decode(), "sha": "c" * 40}
    monkeypatch.setattr(m, "api", fake_api)
    with pytest.raises(m.Blocked, match="FLEET_BLOB_SHA_MISMATCH"):
        m.pinned_source("demo-home", "media_vm", "synthetic-only")


def test_unknown_component_never_reaches_github(monkeypatch):
    m = module()
    monkeypatch.setattr(m.os, "geteuid", lambda: 0)
    monkeypatch.setattr(m.Path, "exists", lambda self: True)
    with pytest.raises(m.Blocked, match="COMPONENT_NOT_ALLOWED"):
        m.reconcile("demo-home", "secrets")


def test_source_code_contains_no_semaphore_level_github_identity():
    text = (ROOT / "bootstrap/dh-site-data-runner.py").read_text()
    assert "api.github.com" not in text
    assert "secrets/fleet.vault.yml" not in text
    assert "gh auth" not in text
