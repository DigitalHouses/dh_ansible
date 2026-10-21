"""Regression checks for Remote Commander pairing task output and error handling."""
from pathlib import Path

PLAY = (Path(__file__).resolve().parents[1] /
        "playbooks/request-runner-remote-commander.yml").read_text()


def test_link_is_displayed_before_work_starts():
    assert "https://mcp.desktopcommander.app/device/verify" in PLAY
    assert PLAY.index("Show authorization link immediately") < PLAY.index(
        "Send fixed-purpose install or pairing request")


def test_user_sees_current_one_time_code_not_old_pairing():
    assert "remote_pairing_record.stdout.startswith('PAIR|' ~ remote_request_id.stdout ~ '|')" in PLAY
    assert "one_time_code: \"{{ remote_pairing_record.stdout.split('|')[3] }}\"" in PLAY
    assert "authorization_url: \"{{ remote_pairing_record.stdout.split('|')[4] }}\"" in PLAY
    assert "when:" in PLAY


def test_failed_root_worker_has_diagnostic_instead_of_service_retry_loop():
    assert "Await managed Remote Commander service" not in PLAY
    assert "remote_request_result.stdout in ['PASS', 'FAILED']" in PLAY
    assert "Report installer failure instead of retrying silently" in PLAY
    assert "remote_request_active.stdout != 'active'" in PLAY
    assert "/var/log/dh-remote-commander-install.log" in PLAY


def test_stale_success_is_not_accepted():
    assert "Capture previous worker status timestamp" in PLAY
    assert "(remote_request_new_status.stat.mtime | float) >" in PLAY
    assert "(remote_request_previous_status.stat.mtime | float)" in PLAY


def test_no_fabricated_code_or_false_connected_status():
    assert "one_time_code: NOT_REQUIRED" in PLAY
    assert "Refuse false success when no active code was issued" in PLAY
    assert "result: SERVICE_ACTIVE" in PLAY
    assert "result: PASS" not in PLAY
