"""File-only regression for secure credential generation, without Samba writes."""
from pathlib import Path
import importlib.util
import stat
import tempfile


def module():
    source = Path(__file__).resolve().parents[1] / "roles/media_guest_setup/files/dh-media-accounts.py"
    spec = importlib.util.spec_from_file_location("dh_media_accounts_unit", source)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_first_run_generates_and_second_run_reuses_password():
    m = module()
    with tempfile.TemporaryDirectory() as temp:
        path = Path(temp) / "owner.txt"
        first, created = m.read_or_create(path, "serzhan")
        assert created and len(first) == 8
        assert all(c in m.ALPHABET for c in first)
        assert stat.S_IMODE(path.lstat().st_mode) == 0o600
        second, created_again = m.read_or_create(path, "serzhan")
        assert not created_again and first == second


def test_symlink_credential_is_rejected_without_disclosing_target():
    m = module()
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        (root / "target").write_text("sensitive")
        (root / "owner.txt").symlink_to(root / "target")
        try:
            m.read_or_create(root / "owner.txt", "serzhan")
        except RuntimeError as e:
            assert "CREDENTIAL_FILE_UNSAFE" in str(e)
        else:
            raise AssertionError("SYMLINK_NOT_REJECTED")
