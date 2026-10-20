#!/usr/bin/env bash
# A fixed root-owned installer; never execute mutable Semaphore YAML as root.
set -Eeuo pipefail
umask 077
REQUEST=/var/lib/semaphore/dh-remote-commander.request
STATUS=/run/dh-remote-commander-install.status
BASE=/opt/digitalhouses/bootstrap/remote-commander
LOG=/var/log/dh-remote-commander-install.log
PAIR_FILE=/run/dh-remote-commander-pairing.status
PAIR_URL=https://mcp.desktopcommander.app/device/verify
UNIT=digitalhouses-remote-commander.service
# Only a sanitized short-lived approval code is readable by Semaphore.
publish_pairing() {
  local state="$1" tmp
  [[ ! -L "$PAIR_FILE" ]] || exit 2
  tmp="$(mktemp /run/dh-remote-pair.XXXXXXXX)"
  printf 'PAIR|%s|%s\n' "$NONCE" "$state" > "$tmp"
  chown root:semaphore "$tmp"
  chmod 0640 "$tmp"
  mv -f -- "$tmp" "$PAIR_FILE"
}
read_pairing() {
  local attempt invocation log url code
  for ((attempt=1; attempt<=45; attempt++)); do
    [[ "$(systemctl is-active "$UNIT")" == active ]] || {
      publish_pairing SERVICE_DOWN
      return
    }
    invocation="$(systemctl show -P InvocationID "$UNIT")"
    if [[ "$invocation" =~ ^[a-f0-9]{32}$ ]]; then
      # 13-minute freshness cutoff vs the provider's 15-minute expiry.
      log="$(journalctl -q --no-pager -o cat --since "@$(($(date +%s) - 780))" \
        "_SYSTEMD_INVOCATION_ID=$invocation" 2>/dev/null || true)"
      if [[ "$log" == *"Authorization successful"* ||
            "$log" == *"Device ready"* ]]; then
        publish_pairing CONNECTED
        return
      fi
      url="$(printf '%s\n' "$log" | grep -Eo \
        'https://mcp\.desktopcommander\.app/device/verify\?user_code=[A-Z0-9]{4}-[A-Z0-9]{4}' \
        | tail -n 1 || true)"
      if [[ -n "$url" ]]; then
        code="$(printf '%s\n' "$url" | sed 's/.*user_code=//')"
        publish_pairing "CODE|$code|$url"
        return
      fi
    fi
    sleep 1
  done
  publish_pairing "WAITING|$PAIR_URL"
}
report() { printf '%s\n' "$1" > "$STATUS"; chmod 0644 "$STATUS"; }
on_exit() { local rc=$?; if (( rc != 0 )); then report FAILED; fi; }
trap on_exit EXIT
[[ "$(id -u)" == "0" ]] || exit 2
[[ -f "$REQUEST" && ! -L "$REQUEST" ]] || exit 2
[[ "$(stat -c '%U:%G:%a' "$REQUEST")" == 'semaphore:semaphore:600' ]] || exit 2
REQUEST_DATA="$(cat "$REQUEST")"
[[ "$REQUEST_DATA" =~ ^(INSTALL|PAIR):([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$ ]] || exit 2
ACTION="${BASH_REMATCH[1]}"
NONCE="${BASH_REMATCH[2]}"
rm -f -- "$REQUEST"
HOST="$(hostname)"
[[ "$HOST" =~ ^ansible-([a-z][a-z0-9]*(-[a-z0-9]+)+)$ ]] || exit 2
HOST_SITE="${BASH_REMATCH[1]}"
SITE_DIR="/var/lib/ansible/site-data"
[[ -d "$SITE_DIR" && ! -L "$SITE_DIR" ]] || exit 2
# Exactly one canonical site inventory per runner; never infer site_id
# from the shorter owner-only hostname.
mapfile -d '' -t SITE_DIRS < <(
  find "$SITE_DIR" -mindepth 1 -maxdepth 1 -type d -print0
)
[[ "${#SITE_DIRS[@]}" -eq 1 ]] || exit 2
SITE="${SITE_DIRS[0]##*/}"
[[ "$SITE" == "$HOST_SITE" ]] || exit 2
OWNER="${SITE%-*}"
TYPE="${SITE##*-}"
META="$SITE_DIR/$SITE/group_vars/all.yml"
INVENTORY="$SITE_DIR/$SITE/hosts.yml"
for file in "$META" "$INVENTORY"; do
  [[ -f "$file" && ! -L "$file" ]] || exit 2
  [[ "$(stat -c '%u' "$file")" == "0" ]] || exit 2
done
grep -Fxq "  id: $SITE" "$META" || exit 2
grep -Fxq "  owner_slug: $OWNER" "$META" || exit 2
grep -Fxq "  type: $TYPE" "$META" || exit 2
for path in "$BASE" "$BASE/playbooks" "$BASE/roles" \
    "$BASE/roles/runner_remote_commander" \
    "$BASE/playbooks/apply-runner-remote-commander.yml"; do
  [[ -e "$path" && ! -L "$path" ]] || exit 2
  [[ "$(stat -c '%u' "$path")" == "0" ]] || exit 2
  [[ "$(stat -c '%a' "$path")" =~ ^(755|644)$ ]] || exit 2
done
[[ -x /opt/ansible/venv/bin/ansible-playbook ]] || exit 2
if [[ "$ACTION" == PAIR ]]; then
  [[ "$(systemctl is-active "$UNIT")" == active ]] || exit 2
  report RUNNING
  read_pairing
  report PASS
  exit 0
fi
: > "$LOG"
chmod 0600 "$LOG"
report RUNNING
export ANSIBLE_ROLES_PATH="$BASE/roles"
export LANG=C.UTF-8 LC_ALL=C.UTF-8
/opt/ansible/venv/bin/ansible-playbook -i "$INVENTORY" \
  "$BASE/playbooks/apply-runner-remote-commander.yml" >>"$LOG" 2>&1
[[ "$(systemctl is-active digitalhouses-remote-commander.service)" == 'active' ]]
read_pairing
report PASS
