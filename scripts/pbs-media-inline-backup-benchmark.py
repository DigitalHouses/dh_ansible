#!/usr/bin/env python3
"""Final A/B: same CT1231 backup into PBS co-located with Plex inside Media VM.

Runs exactly one PVE vzdump per explicit label (full/repeat); never deletes
backups or touches VM disks. Samples same guest CPU/RAM/Hitachi I/O metrics as
earlier standalone PBS PCI measurement. Requires commissioned pinned SSH.
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
MEDIA_HOST = "debian@192.168.11.232"
PVE_KNOWN = "/var/lib/semaphore/.ssh/dh_pve_known_hosts"
MEDIA_KNOWN = "/var/lib/semaphore/.ssh/dh_media_guest_known_hosts"
STORAGE = "pbs-media-pilot"
OUTDIR = Path("/tmp/dh-pbs-media-inline-backup-benchmark")
SAMPLE_SECS = 3
STATS_COMMAND = """head -1 /proc/stat; grep -w 'sdb' /proc/diskstats; grep -E '^Mem(Total|Available):' /proc/meminfo"""


def ssh_args(host):
    known = PVE_KNOWN if host == PVE_HOST else MEDIA_KNOWN
    return [
        "ssh", "-i", SSH_IDENTITY, "-o", "BatchMode=yes",
        "-o", "IdentitiesOnly=yes", "-o", "StrictHostKeyChecking=yes",
        "-o", "UserKnownHostsFile=" + known, "-o", "ConnectTimeout=10",
        host
    ]


def ssh_run(host, command):
    return subprocess.run(ssh_args(host) + [command], check=True, capture_output=True, text=True).stdout.strip()


def must(condition, message):
    if not condition:
        raise RuntimeError(message)


def check(label):
    out = ssh_run(PVE_HOST, "qm status 2232; pct status 1231; pvesm status --storage pbs-media-pilot")
    lines = out.splitlines()
    must(len(lines) >= 4, "short PVE preflight")
    must(lines[:2] == ["status: running", "status: running"], "Media VM/CT1231 not running")
    must("pbs-media-pilot" in out and "active" in out, "PBS Media storage not active")
    must(not ssh_run(PVE_HOST, "test -e /etc/pve/qemu-server/2233.conf && echo PRESENT || echo ABSENT") == "PRESENT",
         "old PBS VM present")
    must(ssh_run(PVE_HOST, "pgrep -x vzdump >/dev/null && echo BUSY || echo IDLE") == "IDLE",
         "another PVE backup already running")
    listed = ssh_run(PVE_HOST, "pvesm list pbs-media-pilot --content backup")
    count = max(0, len(listed.splitlines()) - 1)
    if label == "full":
        must(count == 0, f"fresh datastore already has {count} backups")
    if label == "repeat":
        must(count >= 1, "repeat backup requires initial snapshot")
    check_media = ssh_run(MEDIA_HOST, "hostname -s; uname -r; sudo -n zpool get -H -o value guid serzhan-pool; sudo -n zpool list -H -o health serzhan-pool; sudo -n zfs get -H -o value mounted serzhan-pool/backups/pbs-media-pilot")
    media_lines = check_media.splitlines()
    must(media_lines[0] == "mediaserver-serzhan"
         and "deb13" in media_lines[1]
         and media_lines[2:] == ["40733622006301358", "ONLINE", "yes"],
         "Media guest, Debian kernel or ZFS pool drift: " + repr(media_lines))
    services = ssh_run(MEDIA_HOST, "systemctl is-active proxmox-backup.service proxmox-backup-proxy.service plexmediaserver.service jellyfin.service")
    must(services.splitlines() == ["active"] * 4, "media and PBS services must remain running")


def stats():
    lines = ssh_run(MEDIA_HOST, STATS_COMMAND).splitlines()
    must(len(lines) == 4, "missing Media CPU/disk/memory telemetry")
    cpu = list(map(int, lines[0].split()[1:9]))
    disk = lines[1].split()
    must(disk[2] == "sdb", "wrong physical disk stats device")
    memory = {}
    for line in lines[2:]:
        v = line.split()
        memory[v[0].rstrip(":")] = int(v[1])
    return (
        {
            "cpu_total": sum(cpu), "cpu_idle": cpu[3] + cpu[4],
            "write_ios": int(disk[7]), "read_ios": int(disk[3]),
            "write_sectors": int(disk[9]), "read_sectors": int(disk[5]),
            "io_ms": int(disk[12])
        },
        memory
    )


def sample_row(prev, current, prev_time, now, started):
    a = prev[0]
    b = current[0]
    mem = current[1]
    dt = max(now - prev_time, 0.001)
    cpu_delta = b["cpu_total"] - a["cpu_total"]
    cpu_busy = 100 * (1 - (b["cpu_idle"] - a["cpu_idle"]) / cpu_delta) if cpu_delta > 0 else 0
    return {
        "sec": round(now - started, 2),
        "cpu_percent": round(cpu_busy, 2),
        "mem_used_mib": round((mem["MemTotal"] - mem["MemAvailable"]) / 1024, 1),
        "mem_available_mib": round(mem["MemAvailable"] / 1024, 1),
        "sdb_write_mib_s": round((b["write_sectors"]-a["write_sectors"]) * 512 / 1048576 / dt, 3),
        "sdb_read_mib_s": round((b["read_sectors"]-a["read_sectors"]) * 512 / 1048576 / dt, 3),
        "sdb_write_iops": round((b["write_ios"]-a["write_ios"]) / dt, 1),
        "sdb_read_iops": round((b["read_ios"]-a["read_ios"]) / dt, 1),
        "sdb_io_util_percent": round((b["io_ms"]-a["io_ms"]) / 1000 / dt * 100, 2)
    }


def main(label):
    check(label)
    OUTDIR.mkdir(mode=0o750, parents=True, exist_ok=True)
    log_path = OUTDIR / f"pbs-media-ct1231-{label}-vzdump.log"
    csv_path = OUTDIR / f"pbs-media-ct1231-{label}-metrics.csv"
    report_path = OUTDIR / f"pbs-media-ct1231-{label}-report.json"
    start = time.monotonic()
    previous_stats = stats()
    previous_time = time.monotonic()
    rows = []
    fields = [
        "sec", "cpu_percent", "mem_used_mib", "mem_available_mib",
        "sdb_write_mib_s", "sdb_read_mib_s", "sdb_write_iops",
        "sdb_read_iops", "sdb_io_util_percent"
    ]
    command = ssh_args(PVE_HOST) + [
        "vzdump 1231 --storage pbs-media-pilot --mode snapshot --remove 0"
    ]
    with log_path.open("w", encoding="utf-8") as log, csv_path.open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=fields)
        writer.writeheader()
        process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT)
        while True:
            time.sleep(SAMPLE_SECS)
            now = time.monotonic()
            current_stats = stats()
            row = sample_row(previous_stats, current_stats, previous_time, now, start)
            rows.append(row)
            writer.writerow(row)
            csv_file.flush()
            previous_stats = current_stats
            previous_time = now
            if process.poll() is not None:
                break
        exit_code = process.wait()
    elapsed = time.monotonic() - start
    def avg(key):
        return round(sum(r[key] for r in rows) / len(rows), 3) if rows else 0
    def peak(key):
        return max((r[key] for r in rows), default=0)
    report = {
        "label": label,
        "exit_code": exit_code,
        "duration_seconds": round(elapsed, 2),
        "sample_count": len(rows),
        "sample_interval_seconds": SAMPLE_SECS,
        "storage": STORAGE,
        "source_ctid": 1231,
        "media_vm": 2232,
        "cpu_avg_percent": avg("cpu_percent"),
        "cpu_peak_percent": peak("cpu_percent"),
        "mem_used_avg_mib": avg("mem_used_mib"),
        "mem_used_peak_mib": peak("mem_used_mib"),
        "mem_available_mib_min": min((r["mem_available_mib"] for r in rows), default=0),
        "sdb_write_mib_s_avg": avg("sdb_write_mib_s"),
        "sdb_write_iops_avg": avg("sdb_write_iops"),
        "sdb_io_util_avg_percent": avg("sdb_io_util_percent"),
        "sdb_io_util_peak_percent": peak("sdb_io_util_percent"),
        "vzdump_log": str(log_path),
        "metrics_csv": str(csv_path),
    }
    report_path.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2), flush=True)
    print("BACKUP_LOG_TAIL:")
    print("\n".join(log_path.read_text().splitlines()[-28:]))
    return exit_code


if __name__ == "__main__":
    arg = argparse.ArgumentParser()
    arg.add_argument("label", choices=("full", "repeat"))
    args = arg.parse_args()
    try:
        sys.exit(main(args.label))
    except Exception as exc:
        print("BENCHMARK_BLOCKED_OR_FAILED: " + repr(exc), file=sys.stderr, flush=True)
        sys.exit(2)
