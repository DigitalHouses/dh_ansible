"""The canonical AI execution procedure must stay discoverable in new chats."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNBOOK = ROOT / "docs/runbooks/ai-agent-execution.md"


def test_agent_execution_runbook_is_linked_from_all_entry_points():
    source = RUNBOOK.read_text(encoding="utf-8")
    assert "## 0. Короткая памятка" in source
    for name in ("AGENTS.md", "README.md",
                 "docs/standards/semaphore-task-design.md",
                 "docs/runbooks/media-semaphore-launch.md"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert "ai-agent-execution.md" in text, name
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    assert agents.index("ai-agent-execution.md") < agents.index("## Non-negotiable")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert readme.index("ai-agent-execution.md") < readme.index("## Единая точка входа")


def test_runbook_covers_proven_runner_and_pinned_release_workflow():
    doc = RUNBOOK.read_text(encoding="utf-8")
    for required in (
        "Remote Desktop Commander", "runuser -u semaphore",
        "ANSIBLE_ROLES_PATH", "plan-pve-media-vm.yml",
        "media_vm_action=$VM_ACTION", "media_raw_disk_action=$DISK_ACTION",
        "stage_pve_access_audit", "dh-pve-media-release-verify",
        "MEDIA_WORKER_RELEASE=PASS", "os.replace", "manifest",
        "keep + import", "Acceptance", "security-deny",
        "создаёт UI Task",
    ):
        assert required in doc, required


def test_runbook_examples_have_balanced_code_fences():
    doc = RUNBOOK.read_text(encoding="utf-8")
    assert doc.count("\n```bash\n") == doc.count("\n```\n")
    assert doc.count("\n```bash\n") >= 4
