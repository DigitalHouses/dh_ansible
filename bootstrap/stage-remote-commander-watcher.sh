#!/usr/bin/env bash
# Fixed-purpose root capability for task 40. Never installs/starts remote agent.
# Shared by fresh bootstrap and an explicitly invoked existing-runner upgrade.
set -Eeuo pipefail
umask 077

SITE="${1:?site ID required}"
SHA="${2:?pinned guest checkout SHA required}"
SRC=/opt/digitalhouses/src
BASE=/opt/digitalhouses/bootstrap/remote-commander
REQ=/var/lib/semaphore/dh-remote-commander.request

[[ "$(id -u)" == 0 && ! -f /etc/pve/user.cfg ]] || { echo "BLOCKED: guest root required" >&2; exit 2; }
[[ "$SITE" =~ ^[a-z][a-z0-9]*(-[a-z0-9]+)+$ ]] || { echo "BLOCKED: invalid site" >&2; exit 2; }
[[ "$SHA" =~ ^[0-9a-f]{40}$ ]] || { echo "BLOCKED: expected SHA invalid" >&2; exit 2; }
[[ "$(hostname)" == "ansible-$SITE" ]] || { echo "BLOCKED: runner hostname mismatch" >&2; exit 2; }
[[ -d "$SRC/.git" && ! -L "$SRC" ]] || { echo "BLOCKED: pinned source absent" >&2; exit 2; }
[[ "$(git -C "$SRC" rev-parse HEAD)" == "$SHA" ]] || { echo "BLOCKED: pinned source SHA mismatch" >&2; exit 2; }
[[ -z "$(git -C "$SRC" status --porcelain)" ]] || { echo "BLOCKED: pinned source dirty" >&2; exit 2; }
[[ -f "/var/lib/ansible/site-data/$SITE/hosts.yml" &&
   -f "/var/lib/ansible/site-data/$SITE/group_vars/all.yml" ]] || { echo "BLOCKED: site inventory absent" >&2; exit 2; }
grep -Fxq "  id: $SITE" "/var/lib/ansible/site-data/$SITE/group_vars/all.yml" || { echo "BLOCKED: inventory mismatch" >&2; exit 2; }
[[ "$(systemctl is-active dh-semaphore-catalog-sync.path)" == active ]] || { echo "BLOCKED: task 00 watcher inactive" >&2; exit 2; }
[[ ! -e "$REQ" && ! -L "$REQ" ]] || { echo "BLOCKED: task 40 request pending" >&2; exit 2; }
[[ $(systemctl is-active digitalhouses-remote-commander.service 2>/dev/null || true) != active ]] || { echo "BLOCKED: remote agent already active" >&2; exit 2; }

FILES=(
  playbooks/apply-runner-remote-commander.yml
  roles/runner_remote_commander/defaults/main.yml
  roles/runner_remote_commander/tasks/main.yml
  roles/runner_remote_commander/handlers/main.yml
  roles/runner_remote_commander/templates/digitalhouses-remote-commander.service.j2
)
SOURCES=(bootstrap/remote-commander-install-request.sh
         bootstrap/dh-remote-commander-install.service
         bootstrap/dh-remote-commander-install.path)
DESTS=(/usr/local/sbin/dh-remote-commander-install
       /etc/systemd/system/dh-remote-commander-install.service
       /etc/systemd/system/dh-remote-commander-install.path)
MODES=(700 644 644)

for file in "${FILES[@]}" "${SOURCES[@]}"; do
  [[ -f "$SRC/$file" && ! -L "$SRC/$file" ]] || { echo "BLOCKED: pinned asset absent/linked: $file" >&2; exit 2; }
done
for path in /opt/digitalhouses /opt/digitalhouses/bootstrap "$BASE" \
  "$BASE/playbooks" "$BASE/roles" "$BASE/roles/runner_remote_commander" \
  "$BASE/roles/runner_remote_commander/defaults" \
  "$BASE/roles/runner_remote_commander/tasks" \
  "$BASE/roles/runner_remote_commander/handlers" \
  "$BASE/roles/runner_remote_commander/templates"; do
  [[ ! -L "$path" ]] || { echo "BLOCKED: linked destination: $path" >&2; exit 2; }
done

verify_existing() {
  local source="$1" target="$2" mode="$3"
  [[ ! -L "$target" ]] || { echo "BLOCKED: linked artifact $target" >&2; exit 2; }
  if [[ -e "$target" ]]; then
    [[ -f "$target" && "$(stat -c '%U:%a' "$target")" == "root:$mode" ]] || {
      echo "BLOCKED: foreign artifact $target" >&2; exit 2;
    }
    cmp -s "$source" "$target" || { echo "BLOCKED: altered artifact $target" >&2; exit 2; }
  fi
}
for file in "${FILES[@]}"; do verify_existing "$SRC/$file" "$BASE/$file" 644; done
for i in "${!SOURCES[@]}"; do
  verify_existing "$SRC/${SOURCES[$i]}" "${DESTS[$i]}" "${MODES[$i]}"
done

for file in "${FILES[@]}"; do
  install -D -o root -g root -m 0644 "$SRC/$file" "$BASE/$file"
done
for i in "${!SOURCES[@]}"; do
  install -o root -g root -m "0${MODES[$i]}" "$SRC/${SOURCES[$i]}" "${DESTS[$i]}"
done

systemctl daemon-reload
systemctl enable --now dh-remote-commander-install.path
[[ $(systemctl is-active dh-remote-commander-install.path) == active &&
   $(systemctl is-enabled dh-remote-commander-install.path) == enabled ]] || {
  echo "BLOCKED: task 40 watcher not active/enabled" >&2; exit 2;
}
for file in "${FILES[@]}"; do
  [[ "$(stat -c '%U:%a' "$BASE/$file")" == root:644 ]] || exit 2
  cmp -s "$SRC/$file" "$BASE/$file" || exit 2
done
for i in "${!SOURCES[@]}"; do
  [[ "$(stat -c '%U:%a' "${DESTS[$i]}")" == "root:${MODES[$i]}" ]] || exit 2
  cmp -s "$SRC/${SOURCES[$i]}" "${DESTS[$i]}" || exit 2
done
echo "TASK_40_WATCHER=PASS REMOTE_AGENT_INSTALLED=NO"
