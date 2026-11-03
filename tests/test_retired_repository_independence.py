"""The runtime source must not require any retired GitHub repository."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME_DIRS = ("bootstrap", "roles", "playbooks", "plugins")


def test_no_retired_git_repository_runtime_references():
    blocked = (b"ansimble-2.0", b"DigitalHouses/ansible")
    for directory in RUNTIME_DIRS:
        for path in (ROOT / directory).rglob("*"):
            if not path.is_file() or path.suffix not in (".py", ".sh", ".yml", ".yaml", ""):
                continue
            if path.name.endswith(".pyc"):
                continue
            contents = path.read_bytes()
            for fragment in blocked:
                assert fragment not in contents, (
                    f"Retired repository dependency in {path.relative_to(ROOT)}"
                )


def test_truenas_adopt_checks_source_schema_without_legacy_repository():
    s = (ROOT / "roles/truenas_adopt_audit/tasks/main.yml").read_text()
    assert "truenas_access.source is mapping" in s
    assert "truenas_access.source.repository is defined" in s
