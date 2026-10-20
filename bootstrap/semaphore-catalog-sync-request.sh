#!/usr/bin/env bash
# Fixed-purpose root-owned catalog sync; cannot execute arbitrary commands.
set -Eeuo pipefail
umask 077
REQ=/var/lib/semaphore/dh-semaphore-catalog-sync.request
STATUS=/run/dh-semaphore-catalog-sync.status
LOG=/var/log/dh-semaphore-catalog-sync.log
NONCE=
report() {
  local tmp
  [[ -n "$NONCE" ]] || return 0
  [[ ! -L "$STATUS" ]] || exit 2
  tmp="$(mktemp /run/dh-semaphore-sync.XXXXXXXX)"
  printf '%s|%s\n' "$NONCE" "$1" > "$tmp"
  chown root:semaphore "$tmp"
  chmod 0640 "$tmp"
  mv -f -- "$tmp" "$STATUS"
}
on_exit() {
  local rc=$?
  if (( rc != 0 )); then report FAILED || true; fi
}
trap on_exit EXIT
[[ "$(id -u)" == 0 ]] || exit 2
[[ -f "$REQ" && ! -L "$REQ" ]] || exit 2
[[ "$(stat -c '%U:%G:%a' "$REQ")" == "semaphore:semaphore:600" ]] || exit 2
[[ "$(stat -c %s "$REQ")" -le 64 ]] || exit 2
DATA="$(cat "$REQ")"
[[ "$DATA" =~ ^SYNC:([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$ ]] || exit 2
NONCE="${BASH_REMATCH[1]}"
rm -f -- "$REQ"
[[ "$(hostname)" =~ ^ansible-([a-z][a-z0-9]*(-[a-z0-9]+)+)$ ]] || exit 2
SITE="${BASH_REMATCH[1]}"
[[ -f /var/lib/ansible/site-data/"$SITE"/hosts.yml ]] || exit 2
[[ -f /var/lib/ansible/site-data/"$SITE"/group_vars/all.yml ]] || exit 2
[[ -x /usr/bin/python3 ]] || exit 2
[[ -f /opt/digitalhouses/bootstrap/semaphore-project.py ]] || exit 2
[[ "$(stat -c '%u:%a' /opt/digitalhouses/bootstrap/semaphore-project.py)" == "0:644" ]] || exit 2
[[ -f /root/dh-semaphore-catalog-api-token ]] || exit 2
[[ "$(stat -c '%u:%a' /root/dh-semaphore-catalog-api-token)" == "0:600" ]] || exit 2
: > "$LOG"
chmod 0600 "$LOG"
report RUNNING
export LANG=C.UTF-8 LC_ALL=C.UTF-8
/usr/bin/python3 /opt/digitalhouses/bootstrap/semaphore-project.py \
  --site "$SITE" \
  --admin-token-file /root/dh-semaphore-catalog-api-token \
  --sync-catalog --apply > "$LOG" 2>&1
grep -Fxq "SEMAPHORE_PROJECT=APPLIED" "$LOG" || exit 2
grep -Fxq "NO_INFRASTRUCTURE_TASKS_LAUNCHED=YES" "$LOG" || exit 2
report PASS
