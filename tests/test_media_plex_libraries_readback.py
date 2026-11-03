"""Plex library create acceptance: validate persisted DB, not scanner exit codes."""
from __future__ import annotations

from contextlib import redirect_stdout
import importlib.util
import io
from pathlib import Path
import sqlite3
from types import SimpleNamespace
from unittest.mock import patch

import pytest

SOURCE = (Path(__file__).resolve().parents[1] /
          "roles/media_guest_setup/files/dh-plex-libraries.py")


def load(tmp_path):
    spec = importlib.util.spec_from_file_location("media_plex_library_test", SOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    db = tmp_path / "library.db"
    with sqlite3.connect(db) as con:
        con.executescript("""
            CREATE TABLE library_sections (
              id INTEGER PRIMARY KEY, name TEXT, section_type INTEGER);
            CREATE TABLE section_locations (
              id INTEGER PRIMARY KEY, library_section_id INTEGER, root_path TEXT);
        """)
    module.DB = db
    root = Path("/demo-pool/media")
    item = ("movies", "Фильмы", 1, "tv.plex.agents.movie", str(root / "movies"))
    module.expected = lambda _root: [item]
    return module, root, item


def seed(db, path, section_type=1, section_id=3):
    with sqlite3.connect(db) as con:
        con.execute("INSERT INTO library_sections(id,name,section_type) VALUES(?,?,?)",
                    (section_id, "Фильмы", section_type))
        con.execute("INSERT INTO section_locations(library_section_id,root_path) VALUES(?,?)",
                    (section_id, path))


def test_existing_manual_library_never_edited(tmp_path):
    m, root, item = load(tmp_path)
    seed(m.DB, item[4])
    with patch.object(m.subprocess, "run", side_effect=AssertionError("scanner invoked")):
        out = io.StringIO()
        with redirect_stdout(out):
            assert m.reconcile(root, "apply") is False
        assert out.getvalue() == "PLEX_LIBRARIES=READY\n"


def test_missing_library_check_is_read_only(tmp_path):
    m, root, _ = load(tmp_path)
    with patch.object(m.subprocess, "run", side_effect=AssertionError("scanner invoked")):
        out = io.StringIO()
        with redirect_stdout(out):
            assert m.reconcile(root, "check") is False
        assert out.getvalue() == "PLEX_LIBRARIES=NEEDS_UPDATE\n"


def test_scanner_zero_exit_without_database_insert_never_succeeds(tmp_path):
    m, root, _ = load(tmp_path)
    calls = []
    def fake_run(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout="", stderr="")
    with patch.object(m.subprocess, "run", side_effect=fake_run), \
         patch.object(m.time, "sleep"):
        with pytest.raises(m.Blocked, match="PLEX_LIBRARY_CREATION_NOT_CONFIRMED_movies"):
            m.reconcile(root, "apply")
    assert len(calls) == 4
    assert all("--add-section" in x for x in calls)


def test_silent_first_attempt_retries_and_uses_verified_id(tmp_path):
    m, root, item = load(tmp_path)
    calls = []
    def fake_run(args, **kwargs):
        calls.append(args)
        if "--add-section" in args and sum("--add-section" in x for x in calls) == 2:
            seed(m.DB, item[4], section_id=7)
        return SimpleNamespace(returncode=0, stdout="", stderr="")
    with patch.object(m.subprocess, "run", side_effect=fake_run), \
         patch.object(m.time, "sleep"):
        out = io.StringIO()
        with redirect_stdout(out):
            assert m.reconcile(root, "apply") is True
    assert "PLEX_LIBRARY_CREATED=movies" in out.getvalue()
    assert out.getvalue().endswith("PLEX_LIBRARIES=READY\n")
    assert len([x for x in calls if "--add-section" in x]) == 2
    assert [x for x in calls if "--scan" in x][0][-1] == "7"


def test_wrong_type_same_root_fails_without_repair(tmp_path):
    m, root, item = load(tmp_path)
    seed(m.DB, item[4], section_type=2)
    with patch.object(m.subprocess, "run", side_effect=AssertionError("scanner invoked")):
        with pytest.raises(m.Blocked, match="PLEX_LIBRARY_TYPE_CONFLICT_movies"):
            m.reconcile(root, "apply")


def test_duplicate_same_root_fails_without_repair(tmp_path):
    m, root, item = load(tmp_path)
    seed(m.DB, item[4], section_id=3)
    seed(m.DB, item[4], section_id=4)
    with patch.object(m.subprocess, "run", side_effect=AssertionError("scanner invoked")):
        with pytest.raises(m.Blocked, match="PLEX_DUPLICATE_LIBRARY_ROOT_movies"):
            m.reconcile(root, "apply")
