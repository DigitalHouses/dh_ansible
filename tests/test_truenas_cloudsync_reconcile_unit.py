from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "plugins" / "modules" / "truenas_cloudsync_reconcile.py"

spec = importlib.util.spec_from_file_location("truenas_cloudsync_reconcile", MODULE_PATH)
module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(module)


class FakeClient:
    def __init__(self):
        self.calls = []
        self.next_job = 100

    def call(self, method, params=None):
        self.calls.append((method, params))
        if method == "cloudsync.sync_onetime":
            self.next_job += 1
            return self.next_job
        if method == "core.get_jobs":
            job_id = int(params[0][0][2])
            return [{"id": job_id, "state": "SUCCESS", "logs_excerpt": ""}]
        raise AssertionError(f"Unexpected method: {method}")


def desired_root_task():
    return {
        "description": "Site -> B2",
        "path": "/mnt/site_pool/backups",
        "attributes": {
            "bucket": "backups-bucket",
            "folder": "/site-office",
            "fast_list": False,
        },
        "schedule": {
            "minute": "00",
            "hour": "*/6",
            "dom": "*",
            "month": "*",
            "dow": "*",
        },
        "snapshot": False,
        "include": [],
        "exclude": [],
        "enabled": True,
        "bwlimit": [],
        "transfers": None,
        "direction": "PUSH",
        "transfer_mode": "SYNC",
        "encryption": False,
        "filename_encryption": False,
        "create_empty_src_dirs": False,
        "follow_symlinks": False,
    }


def test_restore_remote_children_uses_child_path_to_avoid_bucket_root_validation():
    client = FakeClient()

    children, job_ids = module.restore_remote_children(
        client,
        desired_root_task(),
        credential_id=7,
        target_listing=[
            {"Name": ".bzEmpty", "IsDir": False, "Size": 0},
            {"Name": "ha", "IsDir": True, "Size": -1},
        ],
        timeout=30,
    )

    assert children == ["ha"]
    assert job_ids == [101]

    sync_calls = [call for call in client.calls if call[0] == "cloudsync.sync_onetime"]
    assert len(sync_calls) == 1
    payload = sync_calls[0][1][0]

    assert payload["direction"] == "PULL"
    assert payload["transfer_mode"] == "COPY"
    assert payload["path"] == "/mnt/site_pool/backups/ha"
    assert payload["attributes"]["bucket"] == "backups-bucket"
    assert payload["attributes"]["folder"] == "site-office/ha"


def test_restore_remote_children_blocks_root_level_user_file():
    with pytest.raises(RuntimeError, match="root-level file"):
        module.restore_remote_children(
            FakeClient(),
            desired_root_task(),
            credential_id=7,
            target_listing=[
                {"Name": "unexpected.tar", "IsDir": False, "Size": 123},
            ],
            timeout=30,
        )


def test_restore_remote_children_blocks_unsafe_child_name():
    with pytest.raises(RuntimeError, match="child name is unsafe"):
        module.restore_remote_children(
            FakeClient(),
            desired_root_task(),
            credential_id=7,
            target_listing=[
                {"Name": "../escape", "IsDir": True, "Size": -1},
            ],
            timeout=30,
        )
