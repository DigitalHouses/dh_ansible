#!/usr/bin/env bash
# Root-owned immutable watcher: only execute pinned generic read-only playbook.
set -Eeuo pipefail
umask 077
REQUEST=/var/lib/semaphore/dh-pve-access-audit.request
STATUS=/run/dh-pve-access-audit.status
LOG=/var/log/dh-pve-access-audit.log
PLAY=/opt/digitalhouses/bootstrap/pve-access/playbooks/audit-pve-access.yml
INV_ROOT=/var/lib/ansible/site-data
[[ "$(id -u)" == 0 ]] || exit 2
[[ -f "$REQUEST" && ! -L "$REQUEST" ]] || exit 2
[[ "$(stat -c '%U:%G:%a' "$REQUEST")" == 'semaphore:semaphore:600' ]] || exit 2
REQUEST_DATA="$(cat "$REQUEST")"
[[ "$REQUEST_DATA" =~ ^AUDIT:([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$ ]] || exit 2
NONCE="${BASH_REMATCH[1]}"
rm -f -- "$REQUEST"
report() { printf '%s:%s\n' "$1" "$NONCE" > "$STATUS"; chmod 0644 "$STATUS"; }
on_exit() { rc=$?; if (( rc != 0 )); then report FAILED; fi; }
trap on_exit EXIT
HOST="$(hostname)"
[[ "$HOST" =~ ^ansible-([a-z][a-z0-9]*(-[a-z0-9]+)+)$ ]] || exit 2
SITE="${BASH_REMATCH[1]}"
INV="$INV_ROOT/$SITE/hosts.yml"
META="$INV_ROOT/$SITE/group_vars/all.yml"
for target in "$PLAY" "$INV" "$META"; do
  [[ -f "$target" && ! -L "$target" ]] || exit 2
  [[ "$(stat -c '%u' "$target")" == 0 ]] || exit 2
done
[[ "$(stat -c '%a' "$PLAY")" == 644 ]] || exit 2
[[ -x /opt/ansible/venv/bin/ansible-playbook ]] || exit 2
report RUNNING
: > "$LOG"; chmod 0600 "$LOG"
export LANG=C.UTF-8 LC_ALL=C.UTF-8
/opt/ansible/venv/bin/ansible-playbook -i "$INV" "$PLAY" >> "$LOG" 2>&1
report PASS
