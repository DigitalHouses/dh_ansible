"""Read-only real Samba authentication gate for owner credential recovery."""
from pathlib import Path
import importlib.util
from unittest import mock

SOURCE = Path(__file__).resolve().parents[1] / "roles/media_guest_setup/files/dh-media-password-rotate.py"


def load():
    spec = importlib.util.spec_from_file_location("media_smb_verify", SOURCE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_current_smb_verify_passes_only_real_login():
    mod = load()
    path = mock.Mock()
    path.parent = Path("/tmp/test-credentials")
    engine = mock.Mock(JOURNAL=".dh-media-password-rotation.json")
    engine.owner_password.return_value = "fakepass8"
    engine.verify_only.return_value = True
    samba = mock.Mock()
    with mock.patch("pathlib.Path.exists", return_value=False), mock.patch("pathlib.Path.is_symlink", return_value=False):
        assert mod.verify_current_samba(path, engine, {"samba": samba}) == "SMB_VERIFIED"
    engine.verify_only.assert_called_once_with(samba, "fakepass8")


def test_current_smb_verify_fails_closed():
    mod = load()
    path = mock.Mock()
    path.parent = Path("/tmp/test-credentials")
    engine = mock.Mock(JOURNAL=".dh-media-password-rotation.json")
    engine.owner_password.return_value = "fakepass8"
    engine.verify_only.return_value = False
    with mock.patch("pathlib.Path.exists", return_value=False), mock.patch("pathlib.Path.is_symlink", return_value=False):
        assert mod.verify_current_samba(path, engine, {"samba": mock.Mock()}) == "SMB_FAILED"


def test_smb_diagnostic_leaks_no_credentials_and_never_changes_them():
    src = SOURCE.read_text()
    segment = src.split("def verify_current_samba(", 1)[1].split("def main():", 1)[0]
    assert "adapter.apply" not in segment
    assert "samba.apply" not in segment
    assert "print(password)" not in segment
    assert "MEDIA_OWNER_SMB_AUTH=PASS" in segment
    assert "MEDIA_OWNER_SMB_AUTH=FAIL" in segment
    assert 'operations.add_argument("--verify-samba", action="store_true")' in src
