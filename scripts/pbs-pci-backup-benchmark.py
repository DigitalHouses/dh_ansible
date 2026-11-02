#!/usr/bin/env python3
"""Controlled live A/B benchmark of PVE CT1231 to PBS PCI/ZFS storage.

Runs exactly the same vzdump flags as the NFS baseline. Samples PBS guest CPU,
available RAM and physical sdb I/O through pinned SSH every 3 seconds.
No data/pool/VM deletion, no credentials printed or embedded in the repo.
"""
import argparse
import csv
import json
from pathlib import Path
import subprocess
import sys
import time

SSH_IDENTITY = "/var/lib/semaphore/.ssh/dh_pve_admin_ed25519"
PVE_HOST = "root@192.168.11.6"
PBS_HOST = "debian@192.168.11.233"
PVE_KNOWN = "/var/lib/semaphore/.ssh/dh_pve_known_hosts"
PBS_KNOWN = "/var/lib/semaphore/.ssh/dh_pbs_guest_known_hosts"
STORAGE = "pbs-pci-pilot"
OUTDIR = Path("/tmp/dh-pbs-pci-backup-benchmark")
SAMPLE_SECS = 3


def ssh_args(host):
    known = PVE_KNOWN if host == PVE_HOST else PBS_KNOWN
    return [
        "ssh", "-i", SSH_IDENTITY,
        "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes",
        "-o", "StrictHostKeyChecking=yes", "-o", "UserKnownHostsFile=" + known,
        "-o", "ConnectTimeout=10", host,
    ]


def ssh_run(host, command):
    return subprocess.run(ssh_args(host)+[command], check=True, capture_output=True, text=True).stdout.strip()


def check(label):
    out = ssh_run(PVE_HOST, "qm status 2232; qm status 2233; pct status 1231; pvesm status --storage pbs-pci-pilot")
    lines = out.splitlines()
    if len(lines) < 5:
        raise RuntimeError("short PVE preflight")
    if lines[:3] != ["status: stopped", "status: running", "status: running"]:
        raise RuntimeError("PVE/media/PBS/CT status unexpected: " + repr(lines[:3]))
    if "pbs-pci-pilot" not in out or "active" not in out:
        raise RuntimeError("PCI storage is not active")
    current = ssh_run(PVE_HOST, "pvesm list pbs-pci-pilot --content backup")
    snapshot_count = max(0, len(current.splitlines())-1)
    if label == "full" and snapshot_count != 0:
        raise RuntimeError("full baseline requires zero backup snapshots in new datastore")
    if label == "repeat" and snapshot_count < 1:
        raise RuntimeError("deduplicating repeat requires existing snapshot")
    pbs = ssh_run(PBS_HOST, "sudo -n zpool list -H -o health serzhan-pool; sudo -n zfs get -H -o value mounted serzhan-pool/backups/pbs-pci-pilot")
    if pbs.splitlines() != ["ONLINE", "yes"]:
        raise RuntimeError("ZFS PCI pool unexpectedly unavailable: " + repr(pbs))


STATS_COMMAND = """head -1 /proc/stat; grep -w 'sdb' /proc/diskstats; grep -E '^Mem(Total|Available):' /proc/meminfo"""


def stats():
    lines = ssh_run(PBS_HOST, STATS_COMMAND).splitlines()
    cpu = list(map(int, lines[0].split()[1:9]))
    disk = lines[1].split()
    if disk[2] != "sdb":
        raise RuntimeError("wrong guest physical disk")
    memory = {}
    for line in lines[2:]:
        parts = line.split()
        memory[parts[0].rstrip(":")] = int(parts[1])
    return (
        {"cpu_total":sum(cpu), "cpu_idle":cpu[3]+cpu[4],
         "write_ios":int(disk[7]), "read_ios":int(disk[3]),
         "write_sectors":int(disk[9]), "read_sectors":int(disk[5]),
         "io_ms":int(disk[12])},
        memory,
    )


def sample_row(prev, cur, prev_time, now, since):
    a = prev[0]
    b = cur[0]
    mem = cur[1]
    dt = now-prev_time
    if dt <= 0:
        dt = 0.001
    c = b["cpu_total"]-a["cpu_total"]
    busy = 100*(1-(b["cpu_idle"]-a["cpu_idle"])/c) if c > 0 else 0
    return {
        "sec":round(now-since,2),
        "cpu_percent":round(busy,2),
        "mem_used_mib":round((mem["MemTotal"]-mem["MemAvailable"])/1024,1),
        "mem_available_mib":round(mem["MemAvailable"]/1024,1),
        "sdb_write_mib_s":round((b["write_sectors"]-a["write_sectors"])*512/1048576/dt,3),
        "sdb_read_mib_s":round((b["read_sectors"]-a["read_sectors"])*512/1048576/dt,3),
        "sdb_write_iops":round((b["write_ios"]-a["write_ios"])/dt,1),
        "sdb_read_iops":round((b["read_ios"]-a["read_ios"])/dt,1),
        "sdb_io_util_percent":round((b["io_ms"]-a["io_ms"])/1000/dt*100,2),
    }


def main(label):
    check(label)
    OUTDIR.mkdir(exist_ok=True, parents=True, mode=0o750)
    logpath = OUTDIR / f"pbs-pci-ct1231-{label}-vzdump.log"
    csvpath = OUTDIR / f"pbs-pci-ct1231-{label}-metrics.csv"
    reportpath = OUTDIR / f"pbs-pci-ct1231-{label}-report.json"
    t0 = time.monotonic()
    last, _ = stats()
    last_stats = (last, {})
    prev_time = t0
    rows = []
    header = ["sec","cpu_percent","mem_used_mib","mem_available_mib",
              "sdb_write_mib_s","sdb_read_mib_s","sdb_write_iops","sdb_read_iops","sdb_io_util_percent"]
    cmd = ssh_args(PVE_HOST)+[
        "vzdump 1231 --storage pbs-pci-pilot --mode snapshot --remove 0"
    ]
    with logpath.open("w", encoding="utf-8") as log, csvpath.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f,fieldnames=header)
        writer.writeheader()
        process = subprocess.Popen(cmd,stdout=log,stderr=subprocess.STDOUT)
        while True:
            time.sleep(SAMPLE_SECS)
            now = time.monotonic()
            try:
                current = stats()
            except Exception as e:
                log.write("\nBENCHMARK_METRICS_ERROR: "+repr(e)+"\n")
                log.flush()
                raise
            row = sample_row(last_stats,current,prev_time,now,t0)
            writer.writerow(row)
            rows.append(row)
            f.flush()
            prev_time,last_stats = now,current
            if process.poll() is not None:
                break
        rc = process.wait()
    walltime = time.monotonic()-t0
    def avg(key):
        return round(sum(r[key] for r in rows)/len(rows),3) if rows else 0
    def peak(key):
        return max((r[key] for r in rows), default=0)
    report = {
        "label":label,
        "exit_code":rc,
        "duration_seconds":round(walltime,2),
        "sample_count":len(rows),
        "interval_seconds":SAMPLE_SECS,
        "storage":STORAGE,
        "source_ctid":1231,
        "cpu_avg_percent":avg("cpu_percent"),
        "cpu_peak_percent":peak("cpu_percent"),
        "mem_used_avg_mib":avg("mem_used_mib"),
        "mem_used_peak_mib":peak("mem_used_mib"),
        "sdb_write_mib_s_avg":avg("sdb_write_mib_s"),
        "sdb_write_iops_avg":avg("sdb_write_iops"),
        "sdb_io_util_avg_percent":avg("sdb_io_util_percent"),
        "sdb_io_util_peak_percent":peak("sdb_io_util_percent"),
        "vzdump_log":str(logpath),
        "metrics_csv":str(csvpath),
    }
    reportpath.write_text(json.dumps(report,indent=2)+"\n")
    print(json.dumps(report,indent=2),flush=True)
    print("BACKUP_LOG_TAIL:")
    print("\n".join(logpath.read_text().splitlines()[-24:]))
    return rc


if __name__ == "__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("label",choices=["full","repeat"])
    args=parser.parse_args()
    try:
        sys.exit(main(args.label))
    except Exception as e:
        print("BENCHMARK_BLOCKED_OR_FAILED: "+repr(e),file=sys.stderr,flush=True)
        sys.exit(2)
