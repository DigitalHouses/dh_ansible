"""Pinned Media worker integrity and repository-to-worker version parity."""
import ast
import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERIFY = ROOT / "bootstrap/dh-pve-media-release-verify.py"


def load():
    spec = importlib.util.spec_from_file_location("media_worker_verify", VERIFY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_task80_reads_verified_private_site_blob_before_native_pve_apply():
    import yaml
    plays = yaml.safe_load((ROOT / "playbooks/plan-pve-media-vm.yml").read_text())
    first = str(plays[0])
    assert "media_site_file.json.sha is match('^[0-9a-f]{40}$')" in first
    assert "site_inventory_git_blob" in first
    assert "MEDIA_SITE_FILE_INVALID" in first
    pve = plays[1]["tasks"]
    texts = str(pve)
    assert "dh-media-pve-preflight.py" in texts
    assert "MEDIA_PVE_HARDWARE_PREFLIGHT_BLOCKED" in texts
    assert "dh-pve-media-deploy.py" in texts
    assert texts.index("MEDIA_PVE_HARDWARE_PREFLIGHT_BLOCKED") < texts.index(
        "dh-pve-media-deploy.py")


def test_bootstrap_publishes_complete_bundle_manifest_last():
    source = (ROOT / "bootstrap/semaphore-host-install.py").read_text()
    ast.parse(source)
    assert "stage_pve_access_audit(checkout, args.expected_commit)" in source
    assert 'assets["roles/" + role + "/" + relative]' in source
    assert 'assets["playbooks/" + playbook]' in source
    assert "media-worker-release.json" in source
    assert "assets[relative]" in source
    assert source.index("target = folder + \"/.media-worker-release.json\"") < source.index("Media worker pinned asset integrity check failed")


def test_worker_detects_wrong_commit_or_mixed_files(tmp_path, monkeypatch):
    module = load()
    root = tmp_path / "worker"
    (root / "roles/media_guest_setup/tasks").mkdir(parents=True)
    (root / "roles/media_guest_setup/files").mkdir(parents=True)
    (root / "playbooks").mkdir()
    payloads = {
        "roles/media_guest_setup/tasks/main.yml": "main",
        "roles/media_guest_setup/tasks/apps.yml": "apps",
        "roles/media_guest_setup/files/dh-sambee-owner.py": "sambee",
        "playbooks/setup-pve-media-guest.yml": "guest",
        "playbooks/media-acceptance-worker.yml": "media81",
        "scripts/media-acceptance.py": "read-only",
        "scripts/dh-task-runner.py": "generic-catalog-only",
    }
    for index in range(30):
        payloads[f"roles/media_guest_setup/files/example{index}.txt"] = str(index)
    for path, value in payloads.items():
        file = root / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(value)
    sha = "a" * 40
    manifest = root / ".media-worker-release.json"
    manifest.write_text(json.dumps({
        "schema": 1, "commit": sha,
        "assets": {path: hashlib.sha256(value.encode()).hexdigest()
                   for path, value in payloads.items()},
    }))
    monkeypatch.setattr(module, "checked_file",
                        lambda path: hashlib.sha256(Path(path).read_bytes()).hexdigest())
    assert module.verify(sha, root=root, manifest=manifest, external={}) == len(payloads)
    import pytest
    with pytest.raises(RuntimeError, match="COMMIT_MISMATCH"):
        module.verify("b" * 40, root=root, manifest=manifest, external={})
    (root / "roles/media_guest_setup/files/dh-sambee-owner.py").write_text("old")
    with pytest.raises(RuntimeError, match="ASSET_MISMATCH"):
        module.verify(sha, root=root, manifest=manifest, external={})
