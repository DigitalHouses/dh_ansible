"""Safety and catalog contract for Semaphore Media 81 acceptance."""
import ast
import json
from pathlib import Path

import yaml

ROOT=Path(__file__).resolve().parents[1]


def test_task81_is_registered_as_independent_acceptance_not_vm_action():
    tasks=json.loads((ROOT/"catalog/semaphore-tasks.json").read_text())["tasks"]
    matches=[t for t in tasks if t["title"]=="81-Медиасервер · Acceptance E2E"]
    assert len(matches)==1
    assert matches[0]["playbook"]=="playbooks/media-acceptance.yml"
    assert matches[0]["view"]=="04 · Приёмка"
    assert "survey_vars" not in matches[0]
    assert sum(x["title"].startswith("80-Медиасервер") for x in tasks)==1


def test_task81_handoff_pinned_to_same_worker_release():
    frontend=yaml.safe_load((ROOT/"playbooks/media-acceptance.yml").read_text())[0]
    tasks=frontend["tasks"]
    source=(ROOT/"bootstrap/pve-access-audit-request.sh").read_text()
    s=str(tasks)
    assert "MEDIA_ACCEPTANCE:" in s
    assert "media81_sha.stdout" in s
    assert "dh-pve-access-audit.request" in s
    assert "media81_nonce.stdout" in s
    assert "Read only this request's report" in [x["name"] for x in tasks]
    assert "MEDIA81_ACCEPTANCE_FAILED_SEE_CHECKLIST_ABOVE" in s
    assert "MEDIA_ACCEPTANCE:" in source
    acceptance=source.split('if [[ "$ACTION" == "MEDIA_ACCEPTANCE" ]] then')[-1]
    assert "dh-pve-media-release-verify" in source
    assert "media-acceptance-worker.yml" in source
    assert "report PASS" in source
    assert "qm destroy" not in source.split('if [[ "$ACTION" == "MEDIA_ACCEPTANCE" ]]; then')[1].split("\nfi\n",1)[0]
    assert "media81_nonce" in (ROOT/"playbooks/media-acceptance-worker.yml").read_text()


def test_root_worker_package_and_release_manifest_cover_acceptance():
    src=(ROOT/"bootstrap/semaphore-host-install.py").read_text()
    assert "scripts/media-acceptance.py" in src
    assert "media-acceptance-worker.yml" in src
    check=(ROOT/"bootstrap/dh-pve-media-release-verify.py").read_text()
    assert "MISSING_MEDIA81_SCRIPT" in check
    assert "MISSING_MEDIA81_WORKER" in check


def test_acceptance_probes_are_read_only_and_never_reveal_credentials():
    path=ROOT/"scripts/media-acceptance.py"
    ast.parse(path.read_text())
    src=path.read_text()
    for unsafe in ("zpool import", "zpool destroy", "qm stop", "qm destroy",
                   "qm set", "systemctl restart", "setfacl", "chmod -R",
                   "library/sections/all/refresh", "smbpasswd", "PASSWORD=", "X-Plex-Token"):
        assert unsafe not in src
    assert "mode=ro" in src
    assert "MEDIA81_RESULT=" in src
    assert "new_file_auto_discovery" in src
    assert "plex_periodic_scan_15min" in src
    assert "filestash_smb_login_form" in src
    assert "def filestash_login_form(host)" in src
    assert "class _NoRedirect" in src
    assert "plex_movie_posters" in src
    assert "native_update_writable_" in src
    assert "plex_movie_metadata" in src
    assert "arr_update_paths" in src
    assert "intel_vaapi_functional" in src
    assert "pve_backup_storage_active" in src


def test_worker_is_report_only_and_does_not_install_software():
    y=yaml.safe_load((ROOT/"playbooks/media-acceptance-worker.yml").read_text())[0]
    assert y["hosts"]=="site_runner"
    assert all("ansible.builtin.apt" not in t and
               "ansible.builtin.systemd_service" not in t
               for t in y["tasks"])
    assert any("media-acceptance.py" in str(t) for t in y["tasks"])
    report=next(t for t in y["tasks"] if t["name"].startswith("Store only"))
    assert report["ansible.builtin.copy"]["mode"]=="0640"
    assert report["ansible.builtin.copy"]["group"]=="semaphore"


def test_movie_posters_probe_is_bounded_and_reports_schema_errors():
    src=(ROOT/"scripts/media-acceptance.py").read_text()
    assert '("zpool","status","-x","serzhan-pool")' not in src
    assert '["zpool","status","-x","serzhan-pool"]' in src
    assert '"/serzhan-pool/media/%"' in src
    assert '"reason":"DB_SCHEMA_MISMATCH"' in src
    assert '"reason":"DB_UNREADABLE"' in src
    assert '"NO_INDEXED_MOVIE_IN_MEDIA_LIBRARY"' in src
    assert 'part.file LIKE ? LIMIT 5000' in src
    assert 'target_healthy=' in src
    assert 'SELECT DISTINCT mi.id, mi.guid' in src
