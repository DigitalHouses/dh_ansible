#!/usr/bin/env python3
"""Verify Plex library roots, repairing missing roots with Plex's own scanner.

The scanner can return exit code zero without actually inserting a section
(first-start race). Never accept exit code or stdout as the postcondition.
Run as the 'plex' user. This program opens the library SQLite DB strictly
read-only and leaves existing sections/names/metadata untouched.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import time
from urllib.parse import quote

DB = Path("/var/lib/plexmediaserver/Library/Application Support/Plex Media Server/Plug-in Support/Databases/com.plexapp.plugins.library.db")
SCANNER = "/usr/lib/plexmediaserver/Plex Media Scanner"
LIBRARIES = (
    ("movies", "Фильмы", 1, "tv.plex.agents.movie"),
    ("tvshows", "Сериалы", 2, "tv.plex.agents.series"),
    ("music", "Музыка", 8, "tv.plex.agents.music"),
)


class Blocked(Exception):
    pass


def need(condition: bool, reason: str) -> None:
    if not condition:
        raise Blocked(reason)


def current_sections() -> dict[str, list[tuple[int, int]]]:
    need(DB.is_file(), "PLEX_LIBRARY_DB_NOT_READY")
    uri = "file:" + quote(str(DB), safe="/") + "?mode=ro"
    try:
        with sqlite3.connect(uri, uri=True, timeout=8) as db:
            db.execute("PRAGMA query_only = ON")
            rows = db.execute(
                "SELECT sl.root_path, ls.id, ls.section_type "
                "FROM section_locations sl "
                "JOIN library_sections ls ON ls.id = sl.library_section_id"
            ).fetchall()
    except sqlite3.Error as exc:
        raise Blocked("PLEX_LIBRARY_DB_READ_FAILED") from exc
    sections: dict[str, list[tuple[int, int]]] = {}
    for path, section_id, section_type in rows:
        sections.setdefault(str(path), []).append((int(section_id), int(section_type)))
    return sections


def expected(root: Path) -> list[tuple[str, str, int, str, str]]:
    need(root.is_absolute() and re.fullmatch(r"/[a-z][a-z0-9_-]{1,62}-pool/media", str(root)) is not None,
         "PLEX_MEDIA_ROOT_INVALID")
    need(root.is_dir() and not root.is_symlink(), "PLEX_MEDIA_ROOT_MISSING")
    result = []
    for folder, title, section_type, agent in LIBRARIES:
        path = root / folder
        need(path.is_dir() and not path.is_symlink() and os.access(path, os.R_OK | os.X_OK),
             "PLEX_LIBRARY_FOLDER_INACCESSIBLE_" + folder)
        result.append((folder, title, section_type, agent, str(path)))
    return result


def find_section(sections: dict[str, list[tuple[int, int]]], path: str, section_type: int) -> int | None:
    observed = sections.get(path, [])
    need(len(observed) <= 1, "PLEX_DUPLICATE_LIBRARY_ROOT_" + path.rsplit("/", 1)[-1])
    if not observed:
        return None
    section_id, actual_type = observed[0]
    need(actual_type == section_type, "PLEX_LIBRARY_TYPE_CONFLICT_" + path.rsplit("/", 1)[-1])
    return section_id


def add_section(title: str, section_type: int, agent: str, path: str) -> int:
    args = [SCANNER, "--add-section", title, "--type", str(section_type),
            "--agent", agent, "--location", path, "--lang", "ru"]
    for attempt in range(4):
        # The scanner sometimes returns success with no actual library.
        # The readback, not the exit status, determines the outcome.
        try:
            subprocess.run(args, capture_output=True, text=True, timeout=90, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise Blocked("PLEX_SCANNER_EXECUTION_FAILED") from exc
        for _ in range(5):
            found = find_section(current_sections(), path, section_type)
            if found is not None:
                return found
            time.sleep(1)
    raise Blocked("PLEX_LIBRARY_CREATION_NOT_CONFIRMED_" + path.rsplit("/", 1)[-1])


def reconcile(root: Path, mode: str) -> bool:
    planned = expected(root)
    before = current_sections()
    missing = [lib for lib in planned if find_section(before, lib[4], lib[2]) is None]
    if mode == "check":
        print("PLEX_LIBRARIES=" + ("NEEDS_UPDATE" if missing else "READY"))
        return False
    need(mode == "apply", "PLEX_LIBRARY_MODE_INVALID")
    created = False
    for folder, title, section_type, agent, path in missing:
        # Another process or the owner may have created it meanwhile.
        section_id = find_section(current_sections(), path, section_type)
        if section_id is None:
            section_id = add_section(title, section_type, agent, path)
            created = True
            print("PLEX_LIBRARY_CREATED=" + folder)
            # Scan only the confirmed, numeric section ID. An empty library
            # still has a valid section; scanner stdout need not include an ID.
            try:
                subprocess.run([SCANNER, "--scan", "--section", str(section_id)],
                               capture_output=True, text=True, timeout=90, check=False)
            except (OSError, subprocess.TimeoutExpired):
                # Existing section is confirmed; autoscan/periodic scan handles
                # later metadata updates. Do not rewrite its state.
                print("PLEX_LIBRARY_SCAN_DEFERRED=" + folder)
    after = current_sections()
    for folder, _, section_type, _, path in planned:
        need(find_section(after, path, section_type) is not None,
             "PLEX_LIBRARY_POSTCHECK_MISSING_" + folder)
    print("PLEX_LIBRARIES=READY")
    return created


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("check", "apply"), required=True)
    parser.add_argument("--media-root", required=True)
    args = parser.parse_args()
    need(os.geteuid() != 0, "PLEX_LIBRARIES_MUST_RUN_UNPRIVILEGED")
    reconcile(Path(args.media_root), args.mode)


if __name__ == "__main__":
    try:
        main()
    except (Blocked, OSError, ValueError) as exc:
        print("PLEX_LIBRARIES=BLOCKED:" + str(exc), file=sys.stderr)
        sys.exit(2)
