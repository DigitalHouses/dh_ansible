#!/usr/bin/env bash
# Explicit, non-destructive one-time commissioning for a pre-task40 Tiny runner.
# Runs from PVE root. Only stages the existing guest's pinned task40 assets.
# Never installs the remote agent, rebuilds CT, or changes Fleet credentials.
set -Eeuo pipefail
umask 077

CTID="${1:?CTID required}"
SITE="${2:?site ID required}"
GUEST_SHA="${3:?expected installed guest SHA required}"
PVE_CIDR="${4:?expected vmbr0 CIDR required}"
RUNNER_CIDR="${5:?expected guest CIDR required}"
STAGE_REV=ca1b0d0d30f05fedeb689fb03dbf69d90d6bd45a

[[ "$(id -u)" == 0 && -f /etc/pve/user.cfg ]] || {
  echo "BLOCKED: PVE root required" >&2; exit 2;
}
[[ "$CTID" =~ ^[1-9][0-9]{2,5}$ &&
   "$SITE" =~ ^[a-z][a-z0-9]*(-[a-z0-9]+)+$ &&
   "$GUEST_SHA" =~ ^[a-f0-9]{40}$ &&
   "$PVE_CIDR" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}/[0-9]{1,2}$ &&
   "$RUNNER_CIDR" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}/[0-9]{1,2}$ ]] || {
  echo "BLOCKED: invalid target arguments" >&2; exit 2;
}
[[ "$(ip -4 -o addr show dev vmbr0 | awk '$3=="inet" {print $4}')" == "$PVE_CIDR" ]] || {
  echo "BLOCKED: PVE host/vmbr0 mismatch" >&2; exit 2;
}
CONF="$(pct config "$CTID")"
grep -Fxq "hostname: ansible-$SITE" <<< "$CONF" || { echo "BLOCKED: guest hostname" >&2; exit 2; }
grep -Fxq 'unprivileged: 1' <<< "$CONF" || { echo "BLOCKED: privileged CT" >&2; exit 2; }
grep -Eq "^rootfs: [a-zA-Z0-9_-]+:vm-$CTID-disk-0(,|$)" <<< "$CONF" || {
  echo "BLOCKED: unexpected guest rootfs" >&2; exit 2;
}
grep '^net0:' <<< "$CONF" | grep -Fq "bridge=vmbr0" || { echo "BLOCKED: guest bridge" >&2; exit 2; }
grep '^net0:' <<< "$CONF" | grep -Fq "ip=$RUNNER_CIDR" || { echo "BLOCKED: guest IP" >&2; exit 2; }
[[ "$(pct status "$CTID")" == 'status: running' ]] || {
  echo "BLOCKED: CT is not running" >&2; exit 2;
}

ACTUAL="$(pct exec "$CTID" -- git -C /opt/digitalhouses/src rev-parse HEAD)"
[[ "$ACTUAL" == "$GUEST_SHA" ]] || { echo "BLOCKED: installed guest Git SHA drift" >&2; exit 2; }
[[ "$(pct exec "$CTID" -- hostname)" == "ansible-$SITE" ]] || {
  echo "BLOCKED: guest runtime hostname mismatch" >&2; exit 2;
}
[[ "$(pct exec "$CTID" -- systemctl is-active dh-semaphore-catalog-sync.path)" == active ]] || {
  echo "BLOCKED: existing task00 watcher is not active" >&2; exit 2;
}
TMP="$(mktemp /tmp/dh-task40-stage.XXXXXXXX)"
trap 'rm -f -- "$TMP"' EXIT
curl -fsSL --retry 2 --max-time 30 \
  "https://raw.githubusercontent.com/DigitalHouses/dh_ansible/$STAGE_REV/bootstrap/stage-remote-commander-watcher.sh" -o "$TMP"
[[ -s "$TMP" && ! -L "$TMP" ]] || { echo "BLOCKED: empty stage asset" >&2; exit 2; }
bash -n "$TMP"

echo "TASK_40_TARGET_VERIFIED=PASS"
pct exec "$CTID" -- bash -s -- "$SITE" "$GUEST_SHA" < "$TMP"
[[ "$(pct exec "$CTID" -- systemctl is-active dh-remote-commander-install.path)" == active ]] || {
  echo "BLOCKED: task40 watcher not active after commissioning" >&2; exit 2;
}
echo "TASK_40_EXISTING_RUNNER=PASS"
echo "REMOTE_AGENT_INSTALLED=NO"
echo "DONE"
echo "TERMINAL STAYS OPEN"
