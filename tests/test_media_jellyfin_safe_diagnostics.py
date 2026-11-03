"""Jellyfin first-run diagnostic never captures credentials or API payloads."""
import importlib.util
from pathlib import Path
import stat
import tempfile
from unittest import mock
import yaml

ROOT=Path(__file__).resolve().parents[1]
PATH=ROOT / "roles/media_guest_setup/files/dh-jellyfin-bootstrap.py"


def load():
    spec=importlib.util.spec_from_file_location("dh_jellyfin_diag",PATH)
    obj=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obj)
    return obj


def test_jellyfin_wizard_stage_paths_are_static_and_token_free():
    j=load()
    assert j.stage_name("POST","/Startup/User")=="POST_STARTUP_USER"
    assert j.stage_name("GET","/System/Info/Public")=="GET_PUBLIC_STATUS"
    assert j.stage_name("POST","/Library/VirtualFolders?name=Sensitive")=="POST_LIBRARY"
    assert "Sensitive" not in j.stage_name("POST","/Library/VirtualFolders?name=Sensitive")


def test_private_runtime_diagnostic_reports_code_not_password():
    j=load()
    with tempfile.TemporaryDirectory() as tmp:
        j.DIAGNOSTIC=Path(tmp)/"status"
        j.STAGE[0]="POST_STARTUP_USER"
        with mock.patch.object(j.os,"geteuid",return_value=0):
            j.record_diagnostic(RuntimeError("JELLYFIN_HTTP_403"))
        assert j.DIAGNOSTIC.stat().st_mode & 0o777==0o600
        saved=j.DIAGNOSTIC.read_text()
        assert saved=="JELLYFIN_STAGE=POST_STARTUP_USER JELLYFIN_ERROR=JELLYFIN_HTTP_403\n"
        j.STAGE[0]="POST_STARTUP_USER"
        with mock.patch.object(j.os,"geteuid",return_value=0):
            j.record_diagnostic(RuntimeError("Password: AbCd1234 token=SECRET"))
        assert "AbCd1234" not in j.DIAGNOSTIC.read_text()
        assert "SECRET" not in j.DIAGNOSTIC.read_text()
        assert "UNCLASSIFIED" in j.DIAGNOSTIC.read_text()
        j.clear_diagnostic()
        assert not j.DIAGNOSTIC.exists()


def test_original_ansible_sensitive_jellyfin_task_remains_no_log():
    tasks=yaml.safe_load(
        (ROOT/"roles/media_guest_setup/tasks/jellyfin_bootstrap.yml").read_text())
    def nested(items):
        for item in items:
            yield item
            for section in ("block", "rescue", "always"):
                yield from nested(item.get(section, []))
    step=next(x for x in nested(tasks) if x["name"]==
              "Complete and verify Jellyfin setup without disclosing owner password")
    assert step["no_log"] is True
    code=PATH.read_text()
    assert "record_diagnostic(e)" in code
    assert "JELLYFIN_SETUP=BLOCKED" in code
    assert "Authorization" in code
