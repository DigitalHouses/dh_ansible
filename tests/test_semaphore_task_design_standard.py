"""Keep operator-task design guidance and live catalog in lockstep."""
from pathlib import Path
import json
import re

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs/standards/semaphore-task-design.md"


def test_task_design_standard_is_linked_from_readme():
    assert DOC.is_file()
    assert "docs/standards/semaphore-task-design.md" in (ROOT / "README.md").read_text()


def test_task_design_standard_lists_exact_catalog_task_numbers():
    text = DOC.read_text()
    catalog = json.loads((ROOT / "catalog/semaphore-tasks.json").read_text())["tasks"]
    actual = [re.match(r"^(\\d\\d)-", task["title"]).group(1) for task in catalog]
    listed = re.findall(r"^\\| \\*\\*(\\d\\d)\\*\\* \\|", text, re.MULTILINE)
    assert len(actual) == len(set(actual)), "Catalog numbering must be unique"
    assert sorted(actual) == sorted(listed), "Update task mechanism registry with catalog"


def test_standard_covers_source_runtime_privileges_and_workflows():
    text = DOC.read_text()
    for term in (
        "site-data", "audit", "plan", "apply", "verify",
        "root-worker", "Fleet Detail", "Semaphore",
        "site_inventory_prepare", "fleet_secrets",
        "UNKNOWN", "one-click", "Workflow",
        "TASK", "nonce", "root:semaphore",
    ):
        assert term.lower() in text.lower(), term
