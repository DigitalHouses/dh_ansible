#!/usr/bin/env python3
"""Task 81: independent, read-only Media Server acceptance. No secrets printed.

This command deliberately never invokes a write-capable Proxmox, Plex, ZFS or
Samba operation. Any failed critical check returns a failing process status.
"""
from __future__ import annotations

import argparse
import ipaddress
import json
from pathlib import Path
import re
import shlex
import subprocess
import sys
import urllib.error
import urllib.request
import ssl

SSH_KEY = "/var/lib/ansible/.ssh/pve_host_write_ed25519"
PVE_KNOWN = "/var/lib/ansible/.ssh/pve_host_audit_known_hosts"
GUEST_KNOWN = "/var/lib/ansible/.ssh/pve_host_write_known_hosts"
TIMEOUT = 20

# Runs under root inside the *already running* media guest, strictly read-only.
# Never inspects passwords, tokens, API keys, or customer file contents.
GUEST_PROBE = r"""
import json,os,pathlib,re,sqlite3,subprocess,xml.etree.ElementTree as ET

def run(args, timeout=12):
    try:
        p=subprocess.run(args, capture_output=True,text=True,timeout=timeout)
        return p.returncode,p.stdout,p.stderr
    except (OSError,subprocess.TimeoutExpired):
        return 124,"","unavailable"

def access_as(user,path,mode):
    return run(["runuser","-u",user,"--","test",mode,path])[0]==0

def service(name):
    return run(["systemctl","is-active",name])[1].strip()=="active"

result={"hostname":os.uname().nodename,"services":{},
        "shared_roots":{},"account_access":{}}
for s in ("plexmediaserver","jellyfin","filestash","smbd","avahi-daemon",
          "nfs-server","radarr","sonarr","prowlarr","qbittorrent","nginx"):
    result["services"][s]=service(s+".service")

root="/serzhan-pool"
for name,leaf in (("Media","media"),("Documents","doc"),
                  ("Backups","backups"),("DigitalHouses","digitalhouses")):
    path=root+"/"+leaf
    result["shared_roots"][name]=os.path.isdir(path)
    result["account_access"][name]={
        "owner_read":access_as("serzhan",path,"-r"),
        "admin_read":access_as("serzhan_admin",path,"-r"),
        "admin_write":access_as("serzhan_admin",path,"-w"),
        "owner_write":access_as("serzhan",path,"-w")}

result["password_files_present"]={
    u:pathlib.Path(root+"/digitalhouses/credentials/"+u+".txt").is_file()
    for u in ("serzhan","serzhan_admin")}
result["zfs_healthy"]=run(["zpool","status","-x"])[1].strip()=="all pools are healthy"
result["zfs_pool_present"]=run(["zpool","list","-H","-o","name"])[1].splitlines()
arc={}
try:
    for line in pathlib.Path("/proc/spl/kstat/zfs/arcstats").read_text().splitlines():
        parts=line.split()
        if len(parts)>2 and parts[0] in ("size","c_max"):arc[parts[0]]=int(parts[2])
except (OSError,ValueError):pass
result["arc"]=arc
try:result["arc_parameter"]=int(pathlib.Path("/sys/module/zfs/parameters/zfs_arc_max").read_text().strip())
except (OSError,ValueError):result["arc_parameter"]=None
result["transcode_slice"]=run(["systemctl","show","dhtranscode.slice","-p","CPUQuotaPerSecUSec"])[1].strip()

# Presence of the DRM node and configured QSV is NOT proof of working Intel GPU.
dri=pathlib.Path("/dev/dri/renderD128")
result["render_device"]=dri.exists() and dri.is_char_device()
result["vaapi"]=run(["runuser","-u","jellyfin","--","vainfo",
                     "--display","drm","--device",str(dri)],timeout=12)[0]==0
rc,out,_=run(["journalctl","-k","-b","--no-pager"],timeout=12)
result["gpu_wedged"]=bool(re.search(r"i915.*(declaring it wedged|GuC initialization failed)",out,re.I))
encoding=pathlib.Path("/etc/jellyfin/encoding.xml")
try:
    root_xml=ET.parse(encoding).getroot()
    result["jellyfin_qsv"]=root_xml.findtext("HardwareAccelerationType")=="qsv"
    result["jellyfin_hevc"]="hevc" in [e.text for e in root_xml.findall("./HardwareDecodingCodecs/string")]
except (OSError,ET.ParseError):
    result["jellyfin_qsv"]=False
    result["jellyfin_hevc"]=False

# Keep only selected non-sensitive values. Never print entire Plex Preferences.xml.
plex_root=pathlib.Path("/var/lib/plexmediaserver/Library/Application Support/Plex Media Server")
try:
    prefs=ET.parse(plex_root/"Preferences.xml").getroot().attrib
    result["plex_scan"]={
        "automatic":prefs.get("FSEventLibraryUpdatesEnabled","1"),
        "periodic":prefs.get("ScheduledLibraryUpdatesEnabled","0"),
        "interval_seconds":int(prefs.get("ScheduledLibraryUpdateInterval","1800"))}
except (OSError,ValueError,ET.ParseError):
    result["plex_scan"]={}

# Plex catalog vs finalized files on disk; do not trigger a scan or touch media.
# Check bounded library roots, not arbitrary download or customer directories.
media_ext={".mkv",".mp4",".avi",".m4v",".mov",".ts"}
db=plex_root/"Plug-in Support/Databases/com.plexapp.plugins.library.db"
result["plex_index"]={"status":"SKIP","missing":0,"checked":0}
try:
    import time
    conn=sqlite3.connect(db.as_uri()+"?mode=ro",uri=True,timeout=5)
    roots=[r[0] for r in conn.execute("SELECT root_path FROM section_locations")]
    catalog={r[0] for r in conn.execute("SELECT file FROM media_parts WHERE file IS NOT NULL")}
    conn.close()
    checked=0; missing=0; recent=0
    for lib in roots:
        path=pathlib.Path(lib)
        if not path.is_dir() or not str(path).startswith("/serzhan-pool/media/"):
            continue
        for base,dirs,files in os.walk(path):
            dirs[:]=[x for x in dirs if not x.startswith(".")]
            for name in files:
                if pathlib.Path(name).suffix.lower() not in media_ext:continue
                p=pathlib.Path(base)/name
                try:st=p.stat()
                except OSError:continue
                if st.st_size<1048576 or time.time()-st.st_mtime<180:continue
                checked+=1
                if str(p) not in catalog:missing+=1
                if time.time()-st.st_mtime < 86400:recent+=1
                if checked>=5000:break
            if checked>=5000:break
        if checked>=5000:break
    result["plex_index"]={"status":"PASS" if checked else "SKIP",
                           "checked":checked,"missing":missing,
                           "recent_24h":recent,
                           "bounded":checked>=5000}
except (OSError,sqlite3.Error):
    result["plex_index"]={"status":"FAIL","missing":-1,"checked":0}
print(json.dumps(result,ensure_ascii=True,separators=(",",":")))
"""

def ssh(host: str, account: str, argv: list[str], *, pve=False):
    known=PVE_KNOWN if pve else "/var/lib/ansible/.ssh/known_hosts"
    opts=["-i",SSH_KEY,"-o","BatchMode=yes","-o","IdentitiesOnly=yes",
          "-o","StrictHostKeyChecking=yes" if pve else "StrictHostKeyChecking=accept-new",
          "-o","UserKnownHostsFile="+known,"-o","ConnectTimeout=7"]
    cmd=["ssh",*opts,account+"@"+host," ".join(map(shlex.quote,argv))]
    return subprocess.run(cmd,text=True,capture_output=True,timeout=TIMEOUT)

def http_check(host,port,path="/"):
    scheme="https" if port==443 else "http"
    context=ssl._create_unverified_context() if port==443 else None
    try:
        with urllib.request.urlopen(f"{scheme}://{host}:{port}{path}",
                                    timeout=5,context=context) as resp:
            return 200<=resp.status<400
    except urllib.error.HTTPError as e:
        return 200<=e.code<400
    except (urllib.error.URLError,TimeoutError,OSError):
        return False

def check(name,success,detail=""):
    result="PASS" if success else "FAIL"
    print("MEDIA81_CHECK="+name+" RESULT="+result+(" DETAIL="+detail if detail else ""),flush=True)
    if not success:FAILURES.append(name)

def skip(name,reason):
    print("MEDIA81_CHECK="+name+" RESULT=SKIP DETAIL="+reason,flush=True)

FAILURES=[]

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--guest-ip",required=True)
    p.add_argument("--pve-ip",required=True)
    p.add_argument("--vmid",required=True,type=int)
    p.add_argument("--hostname",required=True)
    p.add_argument("--pool",required=True)
    a=p.parse_args()
    for addr in (a.guest_ip,a.pve_ip):ipaddress.IPv4Address(addr)
    if not (100 <= a.vmid <= 999999 and re.fullmatch(r"[a-z0-9-]{2,60}",a.hostname)
            and re.fullmatch(r"[a-z][a-z0-9-]+",a.pool)):
        p.error("Invalid constrained site identity")

    try:
        result=ssh(a.pve_ip,"ansible-linux",["sudo","-n","qm","config",str(a.vmid)],pve=True)
        assert result.returncode==0
        cfg=dict(line.split(":",1) for line in result.stdout.splitlines() if ":" in line)
    except (OSError,subprocess.TimeoutExpired,ValueError,AssertionError):
        cfg={}
    check("pve_vm_identity",cfg.get("name","").strip()==a.hostname)
    check("pve_vcpu_2",cfg.get("cores","").strip()=="2")
    check("pve_memory_4096",cfg.get("memory","").strip()=="4096")
    check("pve_balloon_off",cfg.get("balloon","").strip()=="0")
    check("pve_cpu_limit_150",cfg.get("cpulimit","").strip() in ("1.5","1.50"))
    check("pve_cpu_low_priority",cfg.get("cpuunits","").strip()=="50")
    check("pve_i915_passthrough","0000:00:02.0" in cfg.get("hostpci0",""))
    check("pve_sata_passthrough","0000:00:17.0" in cfg.get("hostpci1",""))

    try:
        r=ssh(a.guest_ip,"debian",["sudo","-n","python3","-c",GUEST_PROBE])
        guest=json.loads(r.stdout) if r.returncode==0 else {}
    except (OSError,subprocess.TimeoutExpired,ValueError):
        guest={}
    check("guest_readonly_probe",bool(guest))
    check("guest_identity",guest.get("hostname")==a.hostname)
    checks=guest.get("services",{})
    for name in ("plexmediaserver","jellyfin","filestash","smbd","avahi-daemon",
                 "nfs-server","radarr","sonarr","prowlarr","qbittorrent","nginx"):
        check("service_"+name,checks.get(name) is True)
    for name,expected_port,path in (
        ("portal",80,"/"),("password_https",443,"/account/"),
        ("plex",32400,"/web/"),("jellyfin",8096,"/"),
        ("filestash",8334,"/"),("sambee",8000,"/"),
        ("radarr",7878,"/"),("sonarr",8989,"/"),
        ("prowlarr",9696,"/"),("qbittorrent",8080,"/")):
        check("http_"+name,http_check(a.guest_ip,expected_port,path))
    check("zfs_pool_and_health",a.pool in guest.get("zfs_pool_present",[]) and guest.get("zfs_healthy") is True)
    arc=guest.get("arc",{})
    limit=768*1024*1024
    check("zfs_arc_limit_768",guest.get("arc_parameter")==limit and
          isinstance(arc.get("c_max"),int) and arc["c_max"]<=limit*1.05)
    check("transcode_cpu_100",guest.get("transcode_slice","").endswith("=1s"))
    check("intel_render_node",guest.get("render_device") is True)
    check("intel_gpu_not_wedged",guest.get("gpu_wedged") is False and bool(guest))
    check("intel_vaapi_functional",guest.get("vaapi") is True)
    check("jellyfin_qsv_hevc",guest.get("jellyfin_qsv") is True and guest.get("jellyfin_hevc") is True)
    access=guest.get("account_access",{})
    for name in ("Media","Documents","Backups","DigitalHouses"):
        rights=access.get(name,{})
        check("smb_admin_"+name,rights.get("admin_read") is True and rights.get("admin_write") is True)
    check("smb_owner_no_admin_shares",
          all(access.get(n,{}).get("owner_read") is False
              for n in ("Backups","DigitalHouses")))
    check("smb_owner_media_documents",
          all(access.get(n,{}).get("owner_write") is True
              for n in ("Media","Documents")))
    check("account_passports_present",all(guest.get("password_files_present",{}).values())
          and len(guest.get("password_files_present",{}))==2)
    scan=guest.get("plex_scan",{})
    check("plex_auto_scan_enabled",scan.get("automatic")=="1")
    check("plex_periodic_scan_15min",scan.get("periodic")=="1" and
          scan.get("interval_seconds")==900)
    idx=guest.get("plex_index",{})
    if idx.get("status")=="SKIP":
        skip("plex_indexed_media_files","NO_FINALIZED_MEDIA_CANDIDATES")
    else:
        check("plex_indexed_media_files",
              idx.get("status")=="PASS" and idx.get("missing")==0,
              "checked="+str(idx.get("checked",0))+" missing="+str(idx.get("missing","?")))
    try:
        result=ssh(a.pve_ip,"ansible-linux",
                   ["sudo","-n","showmount","-e",a.guest_ip],pve=True)
        nfs=result.returncode==0 and f"/{a.pool}/backups/pve" in result.stdout
    except (OSError,subprocess.TimeoutExpired):
        nfs=False
    check("pve_nfs_export",nfs)
    try:
        result=ssh(a.pve_ip,"ansible-linux",["sudo","-n","pvesm","status"],pve=True)
        nfs_active=any(line.split()[0]=="dh_media_backups" and
                       len(line.split())>=3 and line.split()[2]=="active"
                       for line in result.stdout.splitlines())
    except (OSError,subprocess.TimeoutExpired):
        nfs_active=False
    check("pve_backup_storage_active",nfs_active)
    skip("smb_client_authentication","REQUIRES_INDEPENDENT_WINDOWS_OR_MACOS_CLIENT")
    skip("new_file_auto_discovery","REQUIRES_A_REAL_FINALIZED_NEW_MEDIA_FILE_AND_TIME_WINDOW")
    skip("playback_direct_play_and_transcode","REQUIRES_MEDIA_CLIENT_AND_TEMPERATURE_OBSERVATION")
    skip("backup_restore","REQUIRES_SEPARATE_EXPLICIT_TEST_PLAN")
    print("MEDIA81_RESULT="+("FAIL" if FAILURES else "PASS")+
          " CRITICAL_FAILURES="+str(len(FAILURES)),flush=True)
    return 2 if FAILURES else 0

if __name__=="__main__":
    try:sys.exit(main())
    except Exception as exc:
        # Fail closed; do not display raw exception (paths/commands may be private).
        print("MEDIA81_RESULT=FAIL CRITICAL_FAILURES=1 REASON="+type(exc).__name__)
        sys.exit(2)
