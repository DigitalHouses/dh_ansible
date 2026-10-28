"""Guard against Filestash startup races during native password rotation.

Tests use fabricated credentials and a temporary configuration file.
No live service, secret, or user password is touched.
"""
from pathlib import Path
import importlib.util
from tempfile import TemporaryDirectory
from unittest import mock


SOURCE = Path(__file__).resolve().parents[1] / "roles/media_guest_setup/files/dh-media-password-adapters.py"


def load():
    spec = importlib.util.spec_from_file_location("filestash_wait_test", SOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_filestash_waits_for_new_hash_after_restart():
    mod = load()
    with TemporaryDirectory() as root:
        env = Path(root) / "filestash.env"
        env.write_text("ADMIN_PASSWORD=old\n")
        recorded = mock.Mock()
        fake_result = mock.Mock(stdout=b":$2y$12$synthetic\n")
        with (mock.patch.object(mod.Filestash, "ENV", env),
              mock.patch.object(mod.Filestash, "verify", side_effect=[True, False, False, True]) as check,
              mock.patch.object(mod, "silent", return_value=True) as restart,
              mock.patch.object(mod.subprocess, "run", return_value=fake_result),
              mock.patch("time.sleep", recorded)):
            mod.Filestash().apply("oldpass8", "newpass8")
        assert check.call_count == 4
        assert recorded.call_count == 2
        restart.assert_called_once()
        assert env.read_text().startswith("ADMIN_PASSWORD=$2y$12$")


def test_filestash_readiness_timeout_fails_closed():
    mod = load()
    with TemporaryDirectory() as root:
        env = Path(root) / "filestash.env"
        env.write_text("ADMIN_PASSWORD=old\n")
        with (mock.patch.object(mod.Filestash, "ENV", env),
              mock.patch.object(mod.Filestash, "verify", side_effect=[True, False, False]),
              mock.patch.object(mod, "silent", return_value=True),
              mock.patch.object(mod.subprocess, "run", return_value=mock.Mock(stdout=b":$2y$12$synthetic\n")),
              mock.patch("time.monotonic", side_effect=[100.0, 121.0]),
              mock.patch("time.sleep") as sleep):
            try:
                mod.Filestash().apply("oldpass8", "newpass8")
            except RuntimeError as error:
                assert str(error) == "FILESTASH_NEW_HASH_NOT_READY"
            else:
                assert False, "Missing bounded readiness limit"
            sleep.assert_not_called()
