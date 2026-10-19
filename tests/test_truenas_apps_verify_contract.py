"""TrueNAS Apps runtime verification must be read-only and verify pinned health."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_verify_has_strict_runtime_gates():
    text = (ROOT / "roles/truenas_apps_verify/tasks/main.yml").read_text()
    assert "item.state == 'RUNNING'" in text
    assert "item.version ==" in text
    assert "truenas_apps_run_usage" in text
    assert "enable_image_updates" in text
    assert "no_log: true" in text
    for forbidden in ("app.create", "app.update", "app.delete", "docker.update", "qm shutdown"):
        assert forbidden not in text


def test_verify_imports_audit():
    text = (ROOT / "playbooks/verify-truenas-apps.yml").read_text()
    assert "import_playbook: audit-truenas-apps.yml" in text
    assert "truenas_apps_verify" in text
