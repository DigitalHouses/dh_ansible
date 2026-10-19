"""TrueNAS Apps write operations must have authorization and scope guards."""
from pathlib import Path


def test_apply_has_explicit_write_gate_and_no_delete_methods():
    text = (Path(__file__).resolve().parents[1] /
            "roles/truenas_apps_apply/tasks/main.yml").read_text()
    assert "truenas_apps_apply_authorized" in text
    assert "truenas_apps_observed.docker_config.pool in [none, truenas_apps.pool]" in text
    assert "app.create" in text
    assert "item[\'values\']" in text
    assert "item.values" not in text
    assert "docker.update" in text
    for forbidden in ("app.delete", "pool.destroy", "pool.export", "pool.create", "qm destroy", "qm stop"):
        assert forbidden not in text
    assert "no_log: true" in text


def test_secret_dependent_apps_are_not_installed_without_vault():
    text = (Path(__file__).resolve().parents[1] /
            "roles/truenas_apps_apply/tasks/main.yml").read_text()
    assert "item.secret_fields | length == 0" in text
    assert "PENDING_SECRETS" in text
