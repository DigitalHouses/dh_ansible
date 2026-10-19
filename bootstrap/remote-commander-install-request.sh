#!/usr/bin/env bash
# A fixed root-owned installer; never execute mutable Semaphore YAML as root.
set -Eeuo pipefail
umask 077
REQUEST=/var/lib/semaphore/dh-remote-commander.request
STATUS=/var/lib/semaphore/dh-remote-commander.status
BASE=/opt/digitalhouses/bootstrap/remote-commander
LOG=/var/log/dh-remote-commander-install.log
report() { printf '%s\n' "$1" > "$STATUS"; chmod 0644 "$STATUS"; }
on_exit() { local rc=$?; if (( rc != 0 )); then report FAILED; fi; }
trap on_exit EXIT
[[ "$(id -u)" == "0" ]] || exit 2
[[ -f "$REQUEST" && ! -L "$REQUEST" ]] || exit 2
[[ "$(stat -c '%U:%G:%a' "$REQUEST")" == 'semaphore:semaphore:600' ]] || exit 2
[[ "$(cat "$REQUEST")" == 'INSTALL' ]] || exit 2
[[ "$(wc -c < "$REQUEST")" -eq 8 ]] || exit 2
rm -f -- "$REQUEST"
HOST="$(hostname)"
[[ "$HOST" =~ ^ansimble-([a-z][a-z0-9]*(-[a-z0-9]+)+)$ ]] || exit 2
SITE="${BASH_REMATCH[1]}"
INVENTORY="/var/lib/ansimble/site-data/$SITE/hosts.yml"
[[ -f "$INVENTORY" && ! -L "$INVENTORY" ]] || exit 2
for path in "$BASE" "$BASE/playbooks" "$BASE/roles" \
    "$BASE/roles/runner_remote_commander" \
    "$BASE/playbooks/apply-runner-remote-commander.yml"; do
  [[ -e "$path" && ! -L "$path" ]] || exit 2
  [[ "$(stat -c '%u' "$path")" == "0" ]] || exit 2
  [[ "$(stat -c '%a' "$path")" =~ ^(755|644)$ ]] || exit 2
done
[[ -x /opt/ansimble/venv/bin/ansible-playbook ]] || exit 2
: > "$LOG"
chmod 0600 "$LOG"
report RUNNING
export ANSIBLE_ROLES_PATH="$BASE/roles"
export LANG=C.UTF-8 LC_ALL=C.UTF-8
/opt/ansimble/venv/bin/ansible-playbook -i "$INVENTORY" \
  "$BASE/playbooks/apply-runner-remote-commander.yml" >>"$LOG" 2>&1
[[ "$(systemctl is-active digitalhouses-remote-commander.service)" == 'active' ]]
report PASS
