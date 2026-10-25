"""Owner commissioning is pinned, site-scoped and does not create guests."""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMM = ROOT / "bootstrap/commission-site-data-provider.py"


def test_commissioning_script_syntax_and_scope():
    source = COMM.read_text()
    ast.parse(source, filename=str(COMM))
    for text in ("SOURCE_COMMIT_MISMATCH", "SOURCE_TREE_NOT_CLEAN",
                 "RUNNER_SITE_MISMATCH", "ROLLBACK_DIR", "RUNNER_FILE_HASH_MISMATCH",
                 "SITE_DATA_COMMISSION=PASS", "stage_pve_access_audit",
                 "stage_site_data_provider"):
        assert text in source
    assert '"1231"' in source
    for forbidden in ("qm create", "qm set", "qm stop", "qm destroy",
                      "pct destroy", "pveum user", "git reset --hard"):
        assert forbidden not in source


def test_commission_no_new_catalog_template():
    import json
    catalog = json.loads((ROOT / "catalog/semaphore-tasks.json").read_text())["tasks"]
    assert len([i for i in catalog if i["title"].startswith("80-")]) == 1
