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
[[ "$REQUEST_DATA" =~ ^(AUDIT|MEDIA_PLAN):([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$ ]] || exit 2
ACTION="${BASH_REMATCH[1]}"
NONCE="${BASH_REMATCH[2]}"
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
if [[ "$ACTION" == "AUDIT" ]]; then
  /opt/ansible/venv/bin/ansible-playbook -i "$INV" "$PLAY" >> "$LOG" 2>&1
else
  MEDIA="$INV_ROOT/$SITE/media_vm.yml"
  [[ -f "$MEDIA" && ! -L "$MEDIA" ]] || exit 2
  [[ "$(stat -c '%U:%G:%a' "$MEDIA")" == 'root:semaphore:640' ]] || exit 2
  [[ -d /opt/digitalhouses/bootstrap/pve-access/roles ]] || exit 2
  export ANSIBLE_ROLES_PATH=/opt/digitalhouses/bootstrap/pve-access/roles
  /opt/ansible/venv/bin/ansible-playbook -i "$INV" "$PLAY" \
    -e pve_media_plan_requested=true \
    -e "pve_media_plan_nonce=$NONCE" >> "$LOG" 2>&1
  [[ -f /run/dh-pve-media-plan.json && ! -L /run/dh-pve-media-plan.json ]] || exit 2
  [[ "$(stat -c '%U:%G:%a' /run/dh-pve-media-plan.json)" == 'root:semaphore:640' ]] || exit 2
fi
report PASS
