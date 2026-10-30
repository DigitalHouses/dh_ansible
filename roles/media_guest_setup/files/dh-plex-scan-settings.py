#!/usr/bin/env python3
"""Safely reconcile native Plex media scan policy, not the library database.

Runs as root, only on the fixed Plex Preferences.xml path; never prints or
changes Plex tokens, account owner or other preferences. Must not edit a
configuration while the Plex process is running.
"""
import argparse
import os
from pathlib import Path
import pwd
import stat
import subprocess
import tempfile
import xml.etree.ElementTree as ET

PREF=Path("/var/lib/plexmediaserver/Library/Application Support/Plex Media Server/Preferences.xml")
EXPECTED={
    "FSEventLibraryUpdatesEnabled":"1",
    "FSEventLibraryPartialScanEnabled":"1",
    "ScheduledLibraryUpdatesEnabled":"1",
    "ScheduledLibraryUpdateInterval":"900",
}

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--mode",choices=("check","apply"),required=True)
    args=parser.parse_args()
    if PREF.is_symlink() or not PREF.is_file():
        raise RuntimeError("MEDIA_PLEX_PREFERENCES_MISSING")
    meta=PREF.stat()
    if not stat.S_ISREG(meta.st_mode) or meta.st_uid!=pwd.getpwnam("plex").pw_uid:
        raise RuntimeError("MEDIA_PLEX_PREFERENCES_OWNERSHIP_DRIFT")
    tree=ET.parse(PREF)
    root=tree.getroot()
    if root.tag!="Preferences":
        raise RuntimeError("MEDIA_PLEX_PREFERENCES_FORMAT_DRIFT")
    needs={k:v for k,v in EXPECTED.items() if root.get(k)!=v}
    if args.mode=="check":
        print("MEDIA_PLEX_SCAN="+("NEEDS_UPDATE" if needs else "READY"))
        return
    if subprocess.run(["systemctl","is-active","--quiet","plexmediaserver.service"]).returncode==0:
        raise RuntimeError("MEDIA_PLEX_SCAN_REFUSES_LIVE_SETTINGS_EDIT")
    if not needs:
        print("MEDIA_PLEX_SCAN=READY")
        return
    for k,v in needs.items():root.set(k,v)
    fd,name=tempfile.mkstemp(prefix=".dh-media-plex-settings-",dir=PREF.parent)
    try:
        with os.fdopen(fd,"wb") as f:
            f.write(ET.tostring(root,encoding="utf-8",xml_declaration=True))
            f.write(b"\n")
            f.flush()
            os.fsync(f.fileno())
        os.chown(name,meta.st_uid,meta.st_gid)
        os.chmod(name,stat.S_IMODE(meta.st_mode))
        os.replace(name,PREF)
    finally:
        if os.path.exists(name):os.unlink(name)
    print("MEDIA_PLEX_SCAN=CHANGED")

if __name__=="__main__":
    main()
