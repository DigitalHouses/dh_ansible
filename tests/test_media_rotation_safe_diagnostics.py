"""No-secret root-only recovery diagnostics contract."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "roles/media_guest_setup/files/dh-media-password-rotate.py"


def test_recovery_diagnostics_are_read_only_and_mutually_exclusive():
    source = SRC.read_text()
    assert 'operations.add_argument("--diagnose", action="store_true")' in source
    assert "parser.add_mutually_exclusive_group()" in source
    block = source.split("def diagnose(path, engine, steps):", 1)[1].split("def main():", 1)[0]
    assert "engine.check_file(journal_path)" in block
    assert "engine.verify_only(adapter, old)" in block
    assert "engine.verify_only(adapter, new)" in block
    assert '("BOTH" if old_ok and new_ok else' in block
    assert 'else "NEITHER"' in block
    assert "adapter.apply(" not in block
    assert "engine.execute(" not in block
    assert "print(old)" not in block
    assert "print(new)" not in block
    assert "print(record)" not in block
    assert 'ADAPTER_' in block


def test_error_messages_are_finite_vetted_internal_codes_only():
    source = SRC.read_text()
    assert 'type(exc).__name__ == "Blocked"' in source
    assert 're.fullmatch(r"ROTATION_[A-Z0-9_]{4,96}", reason)' in source
    assert "BLOCKED_" in source
