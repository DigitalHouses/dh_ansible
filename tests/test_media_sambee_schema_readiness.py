"""Sambee firstboot DB migration race must retry only transient schema absence."""
from pathlib import Path

import yaml
from jinja2 import Environment

ROOT = Path(__file__).resolve().parents[1]
TASKS = ROOT / "roles/media_guest_setup/tasks/apps.yml"


def task():
    items = yaml.safe_load(TASKS.read_text())
    matches = [item for item in items if item["name"] ==
               "Inspect whether empty preexisting Sambee admin state needs recovery"]
    assert len(matches) == 1
    return matches[0]


def retry_condition(rc, stderr):
    until = task()["until"]
    return Environment().compile_expression(until)(
        media_sambee_firstboot_recovery={"rc": rc, "stderr": stderr})


def test_only_initial_schema_absence_triggers_bounded_retry():
    t = task()
    assert t["retries"] == 30
    assert t["delay"] == 2
    assert t["changed_when"] is False
    assert t["failed_when"] == "media_sambee_firstboot_recovery.rc != 0"
    assert retry_condition(2,
        "SAMBEE_RECOVERY=BLOCKED_SAMBEE_RECOVERY_EXPECTED_TABLE_MISSING") is False
    assert retry_condition(0, "") is True


def test_foreign_identity_and_unsafe_data_do_not_retry():
    for error in (
        "SAMBEE_RECOVERY=BLOCKED_SAMBEE_RECOVERY_FOREIGN_IDENTITY",
        "SAMBEE_RECOVERY=BLOCKED_SAMBEE_RECOVERY_UNKNOWN_PERSISTENT_DATA",
        "SAMBEE_RECOVERY=BLOCKED_SAMBEE_RECOVERY_SYMLINK",
        "SAMBEE_RECOVERY=BLOCKED_RUNTIME",
    ):
        assert retry_condition(2, error) is True
