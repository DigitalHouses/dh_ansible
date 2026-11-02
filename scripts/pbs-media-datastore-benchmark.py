#!/usr/bin/env python3
"""Benchmark first-time PBS datastore creation on a preverified, empty ZFS dataset.

Runs only the literal PBS datastore initialization command. No disk formatting,
ZFS import, pool/dataset creation or deletion, and no external credentials.
Captures per-3-second Linux CPU, memory and selected block-device counters.
Requires root in the same existing Media VM that runs Plex and Jellyfin, verified by an independently scoped Ansible pilot.
"""
import csv
import json
import os
from pathlib import Path
import subprocess
import sys
import time

NAME = "pbs-media-pilot"
POOL = "serzhan-pool"
DATASET = f"{POOL}/backups/{NAME}"
PATH = Path(f"/{POOL}/backups/{NAME}")
TARGET_WWN = "wwn-0x5000cca22bc6ef0d-part1"
DISK = "sdb"
RUN_LABEL = "pbs-media-initialization-2026-11-03"
OUTPUT_DIR = Path("/var/log/digitalhouses-pbs-media-pilot")
SAMPLE_SECONDS = 3


def run(*args):
    return subprocess.run(args, check=True, capture_output=True, text=True).stdout.strip()


def diskstats():
    for line in Path("/proc/diskstats").read_text().splitlines():
        v = line.split()
        if len(v) >= 14 and v[2] == DISK:
            return {
                "reads": int(v[3]),
                "read_sectors": int(v[5]),
                "writes": int(v[7]),
                "write_sectors": int(v[9]),
                "io_ms": int(v[12]),
            }
    raise RuntimeError(f"missing {DISK} from /proc/diskstats")


def cpu():
    v = Path("/proc/stat").read_text().splitlines()[0].split()[1:]
    v = list(map(int, v))
    total = sum(v[:8])
    idle = v[3] + v[4]
    return total, idle


def memory():
    data = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        if line.startswith(("MemTotal:", "MemAvailable:")):
            key, remainder = line.split(":", 1)
            data[key] = int(remainder.strip().split()[0])
    return data


def must(condition, message):
    if not condition:
        raise RuntimeError(message)


def preflight():
    must(os.geteuid() == 0, "requires root inside Media VM")
    must(run("hostname", "-s") == "mediaserver-serzhan", "wrong guest")
    must("deb13" in run("uname", "-r"), "Debian kernel missing/replaced")
    must(run("zpool", "list", "-H", "-o", "guid", POOL) == "40733622006301358", "wrong fresh pool GUID")
    must(run("zpool", "list", "-H", "-o", "health", POOL) == "ONLINE", "pool not healthy")
    pool_status = run("zpool", "status", "-P", POOL)
    must(TARGET_WWN in pool_status and "errors: No known data errors" in pool_status, "wrong disk")
    must(run("readlink", "-f", f"/dev/disk/by-id/{TARGET_WWN[:-6]}") == "/dev/sdb", "wrong physical disk")
    must(run("zfs", "get", "-H", "-o", "value", "mounted", DATASET) == "yes", "dataset not mounted")
    must(run("zfs", "get", "-H", "-o", "value", "atime", DATASET) == "on", "atime not enabled")
    must(run("zfs", "get", "-H", "-o", "value", "mountpoint", DATASET) == str(PATH), "mountpoint mismatch")
    must(run("findmnt", "-n", "-o", "SOURCE", "--mountpoint", str(PATH)) == DATASET, "not on ZFS")
    must(run("zfs", "list", "-H", "-o", "name", f"{POOL}/media") == f"{POOL}/media", "media missing")
    must(run("zfs", "get", "-H", "-o", "value", "mounted", f"{POOL}/media") == "yes", "media unexpectedly unmounted")
    must(run("zfs", "list", "-H", "-o", "name", f"{POOL}/backups") == f"{POOL}/backups", "backup parent missing")
    must(PATH.is_dir(), "new datastore directory missing")
    st = PATH.stat()
    must(st.st_uid == 34 and st.st_gid == 34, "dataset must be owned by PBS backup user")
    must(not any(PATH.iterdir()), "datastore directory is not empty; never reuse for clean test")
    stores = json.loads(run("proxmox-backup-manager", "datastore", "list", "--output-format", "json"))
    must(not any(entry["name"] == NAME for entry in stores), "PBS datastore already exists")
    must(run("zfs", "get", "-H", "-o", "value", "refquota", DATASET) == "128G", "wrong refquota")
    for service in ("proxmox-backup.service", "proxmox-backup-proxy.service",
                    "plexmediaserver.service", "jellyfin.service"):
        must(run("systemctl", "is-active", service) == "active", f"service not active: {service}")

def create():
    preflight()
    OUTPUT_DIR.mkdir(mode=0o750, parents=True, exist_ok=True)
    csv_path = OUTPUT_DIR / f"{RUN_LABEL}.csv"
    cmdlog_path = OUTPUT_DIR / f"{RUN_LABEL}.command.log"
    report_path = OUTPUT_DIR / f"{RUN_LABEL}.json"
    headers = ["seconds", "cpu_percent", "mem_used_mib", "mem_available_mib",
               "sdb_write_mib_s", "sdb_read_mib_s", "sdb_write_iops", "sdb_read_iops", "sdb_io_util_percent"]
    results = []
    t0 = time.monotonic()
    prev_time, prev_disk, prev_cpu = t0, diskstats(), cpu()
    with open(cmdlog_path, "w", encoding="utf-8") as command_log, open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        command = [
            "proxmox-backup-manager", "datastore", "create", NAME, str(PATH),
            "--comment", "DigitalHouses 2026-11-03 PBS inside Media VM; fresh PCI ZFS dataset"
        ]
        proc = subprocess.Popen(command, stdout=command_log, stderr=subprocess.STDOUT)
        while True:
            time.sleep(SAMPLE_SECONDS)
            now = time.monotonic()
            cur_disk, cur_cpu, mem = diskstats(), cpu(), memory()
            dt = now - prev_time
            cpu_total = cur_cpu[0] - prev_cpu[0]
            cpu_idle = cur_cpu[1] - prev_cpu[1]
            row = {
                "seconds": round(now-t0, 2),
                "cpu_percent": round(100 * (1-cpu_idle/cpu_total),2) if cpu_total > 0 else 0,
                "mem_used_mib": round((mem["MemTotal"]-mem["MemAvailable"])/1024,1),
                "mem_available_mib": round(mem["MemAvailable"]/1024,1),
                "sdb_write_mib_s": round((cur_disk["write_sectors"]-prev_disk["write_sectors"])*512 / 1048576/dt,3),
                "sdb_read_mib_s": round((cur_disk["read_sectors"]-prev_disk["read_sectors"])*512 / 1048576/dt,3),
                "sdb_write_iops": round((cur_disk["writes"]-prev_disk["writes"])/dt,1),
                "sdb_read_iops": round((cur_disk["reads"]-prev_disk["reads"])/dt,1),
                "sdb_io_util_percent": round((cur_disk["io_ms"]-prev_disk["io_ms"])/1000/dt*100,2),
            }
            results.append(row)
            writer.writerow(row)
            f.flush()
            prev_time, prev_disk, prev_cpu = now, cur_disk, cur_cpu
            if proc.poll() is not None:
                break
        returncode = proc.wait()
    walltime = time.monotonic()-t0
    chunks = PATH / ".chunks"
    directory_count = sum(1 for item in chunks.iterdir() if item.is_dir()) if chunks.is_dir() else 0
    summary = {
        "command_exit_code": returncode,
        "duration_seconds": round(walltime,2),
        "sample_interval_seconds": SAMPLE_SECONDS,
        "sample_count": len(results),
        "pool": POOL,
        "dataset": DATASET,
        "new_datastore": NAME,
        "new_datastore_path": str(PATH),
        "disk": DISK,
        "chunk_directory_count": directory_count,
        "cpu_avg_percent": round(sum(r["cpu_percent"] for r in results)/len(results),2) if results else 0,
        "cpu_peak_percent": max((r["cpu_percent"] for r in results), default=0),
        "mem_used_avg_mib": round(sum(r["mem_used_mib"] for r in results)/len(results),1) if results else 0,
        "mem_used_peak_mib": max((r["mem_used_mib"] for r in results), default=0),
        "sdb_write_mib_s_avg": round(sum(r["sdb_write_mib_s"] for r in results)/len(results),3) if results else 0,
        "sdb_write_iops_avg": round(sum(r["sdb_write_iops"] for r in results)/len(results),2) if results else 0,
        "sdb_io_util_avg_percent": round(sum(r["sdb_io_util_percent"] for r in results)/len(results),2) if results else 0,
        "sdb_io_util_peak_percent": max((r["sdb_io_util_percent"] for r in results), default=0),
        "csv": str(csv_path),
        "command_log": str(cmdlog_path),
    }
    report_path.write_text(json.dumps(summary, indent=2)+"\n")
    print(json.dumps(summary, indent=2), flush=True)
    return 0 if returncode == 0 and directory_count == 65536 else 2


if __name__ == "__main__":
    try:
        sys.exit(create())
    except Exception as exc:
        print("BENCHMARK_BLOCKED_OR_FAILED: " + repr(exc), file=sys.stderr, flush=True)
        sys.exit(3)
