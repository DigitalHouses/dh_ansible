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


def test_task80_must_send_checkout_sha_before_root_vm_writes():
    source = (ROOT / "playbooks/plan-pve-media-vm.yml").read_text()
    worker = (ROOT / "bootstrap/pve-access-audit-request.sh").read_text()
    assert "media_checkout_sha.stdout" in source
    assert "MEDIA_DEPLOY_REPOSITORY_PIN_MISSING" in source
    assert 'SOURCE_SHA="${BASH_REMATCH[5]}"' in worker
    assert '--expected-commit "$SOURCE_SHA"' in worker
    assert worker.index("dh-pve-media-release-verify") < worker.index("dh-pve-media-deploy \\")


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
