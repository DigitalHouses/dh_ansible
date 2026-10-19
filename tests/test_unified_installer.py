"""Offline contract: public single-repository installer without private credentials."""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
INSTALL = ROOT / "bootstrap/install.sh"


def test_installer_is_present_and_pins_independent_immutable_inputs():
    s = INSTALL.read_text()
    assert re.search(r'^CODE_RELEASE="[a-f0-9]{40}"$', s, re.M)
    assert re.search(r'^CODE_INSTALLER_BLOB="[a-f0-9]{40}"$', s, re.M)
    assert re.search(r'^FLEET_RELEASE="[a-f0-9]{40}"$', s, re.M)
    assert re.search(r'^GH_SHA256="[a-f0-9]{64}"$', s, re.M)
    assert "repos/DigitalHouses/dh_ansible/contents/bootstrap/semaphore-host-install.py?ref=$CODE_RELEASE" in s
    assert "CODE_INSTALLER_BLOB" in s and "identity != expected_sha" in s
    assert "--expected-fleet-commit \"$FLEET_RELEASE\"" in s
    assert "--use-github-session --apply" in s
    assert "https://github.com/login/device" in s
    assert "dh_ansimble_fleet_detail" not in s
    assert "ansimble-installer" not in s
    assert "SITE_CONFIRMATION=YES $SITE" in s
    assert "confirm_site_id || exit 2" in s
    assert "Введите Site ID (<owner>-<type>)" in s
    assert "Site ID '$candidate' указан верно? (y/n)" in s
    assert "case \"$answer\"" in s
    assert '  --site "$SITE"' in s
    assert 'echo "OBJECT_SLUG=$candidate"' in s
    assert 'if (( $# == 1 ))' in s
    assert "Site ID is entered interactively" in s


def test_installer_blocks_occupied_ct_and_has_no_destructive_actions():
    s = INSTALL.read_text()
    assert "EUID != 0" in s
    assert "/etc/pve/user.cfg" in s
    assert "--resume-semaphore" in s and "--resume-ansible" in s
    assert "rm -rf \"$WORK\"" in s
    assert "pct destroy" not in s
    assert "qm destroy" not in s
    assert "pveam remove" not in s
    assert "GH_CONFIG_DIR=\"$WORK/gh-config\"" in s


def _site_confirm_script():
    """Extract the actual shell function without running PVE, OAuth or Vault."""
    s = INSTALL.read_text()
    begin = s.index("confirm_site_id() {")
    # The prompt function is isolated from the root-only journaling prologue.
    end = s.index("\n}\n", begin) + 2
    return (
        "set -Eeuo pipefail\nSITE=''\n" +
        s[begin:end] +
        "\nconfirm_site_id\nprintf 'CONFIRMED_SITE=%s\\n' \"$SITE\"\n"
    )


def _run_site_confirm(responses):
    import subprocess
    return subprocess.run(
        ["bash", "-c", _site_confirm_script()],
        input=responses,
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )


def test_site_typo_can_be_corrected_after_n():
    result = _run_site_confirm("serzahn-home\nn\nserzhan-home\ny\n")
    assert result.returncode == 0, result.stderr
    assert "SITE_CONFIRMATION=YES serzhan-home" in result.stdout
    assert "Повторный ввод Site ID" in result.stdout
    assert "CONFIRMED_SITE=serzhan-home" in result.stdout
    assert "CONFIRMED_SITE=serzahn-home" not in result.stdout


def test_malformed_site_id_reprompts_without_confirmation():
    result = _run_site_confirm("bad_value\nserzhan-home\ny\n")
    assert result.returncode == 0, result.stderr
    assert "Некорректный Site ID" in result.stdout
    assert "CONFIRMED_SITE=serzhan-home" in result.stdout
    assert "SITE_CONFIRMATION=YES bad_value" not in result.stdout


def test_unrecognized_answer_repeats_confirmation():
    result = _run_site_confirm("serzhan-home\nmaybe\nY\n")
    assert result.returncode == 0, result.stderr
    assert "Ответьте y или n" in result.stdout
    assert "CONFIRMED_SITE=serzhan-home" in result.stdout


def test_site_confirmation_eof_blocks_without_downstream():
    for supplied in ("", "serzhan-home\n", "serzhan-home\nn\n"):
        result = _run_site_confirm(supplied)
        assert result.returncode != 0
        assert "CONFIRMED_SITE=" not in result.stdout
        assert "BLOCKED:" in result.stderr

def test_site_id_trims_only_edge_whitespace():
    result = _run_site_confirm("  serzhan-home  \r\ny\n")
    assert result.returncode == 0, result.stderr
    assert "SITE_INPUT_TRIMMED=YES" in result.stdout
    assert "CONFIRMED_SITE=serzhan-home" in result.stdout


def test_site_id_never_normalizes_inner_spaces():
    result = _run_site_confirm("serzhan -home\nserzhan-home\ny\n")
    assert result.returncode == 0, result.stderr
    assert "Некорректный Site ID" in result.stdout
    assert "CONFIRMED_SITE=serzhan-home" in result.stdout



def test_installer_has_root_only_structured_journal_before_site_prompt():
    s = INSTALL.read_text()
    assert 'LOG_DIR="/var/log/digitalhouses/ansible"' in s
    assert 'LOG_FILE=$(mktemp "$LOG_DIR/install-' in s
    assert 'chmod 0600 "$LOG_FILE"' in s
    assert 'stat -c' in s
    assert 'LOG_MODE=SAFE_EVENTS_ONLY' in s
    assert 'trap installer_cleanup EXIT' in s
    assert s.index('LOG_FILE=$(mktemp') < s.index('confirm_site_id || exit 2')
    assert 'grep -aE --line-buffered "$LOG_PATTERN"' in s
    assert 'RESULT_RC=$rc' in s
    assert 'LOG_FILE=$LOG_FILE' in s


def test_installer_journal_excludes_auth_codes_and_unstructured_output():
    s = INSTALL.read_text()
    pattern = re.search(r"^LOG_PATTERN='([^']+)'$", s, re.M)
    assert pattern, "Expected strict journal allowlist"
    allow = re.compile(pattern.group(1))
    assert allow.match("SEMAPHORE_SYSTEMD=PASS")
    assert allow.match("SITE_INVENTORY_DIR_CREATE=/safe/example")
    assert allow.match("PCT_EXEC_FAILURE=command:install rc:129")
    assert not allow.match("! First copy your one-time code: ABCD-EFGH")
    assert not allow.match("OWNER: Fleet master password (hidden): example")
    assert not allow.match("GH_TOKEN=anything")
    assert not allow.match("ghp_" + "x" * 30)
    assert allow.match("BLOCKED: Command failed: pct, rc=129")
