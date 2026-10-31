#!/usr/bin/env bash
# Guest-only handoff: local runner inventory, single Semaphore task 00 and its watcher.
set -Eeuo pipefail
umask 077

SITE="${1:?site ID required}"
SHA="${2:?pinned Git revision required}"
SRC=/opt/digitalhouses/src
BASE="/var/lib/ansible/site-data/$SITE"
[[ $EUID -eq 0 && ! -f /etc/pve/user.cfg ]] || { echo "BLOCKED: guest root required" >&2; exit 2; }
[[ $SITE =~ ^[a-z][a-z0-9]*(-[a-z0-9]+)+$ ]] || { echo "BLOCKED: invalid site" >&2; exit 2; }
[[ $SHA =~ ^[0-9a-f]{40}$ && $(hostname) == "ansible-$SITE" ]] || { echo "BLOCKED: guest/revision mismatch" >&2; exit 2; }
[[ $(git -C "$SRC" rev-parse HEAD) == "$SHA" ]] || { echo "BLOCKED: Git commit mismatch" >&2; exit 2; }
[[ -f /root/dh-semaphore-initial-admin-password ]] || { echo "BLOCKED: Semaphore not installed" >&2; exit 2; }

OWNER="${SITE%-*}"
TYPE="${SITE##*-}"
for folder in /var/lib/ansible /var/lib/ansible/site-data "$BASE" "$BASE/group_vars"; do
  [[ ! -L $folder ]] || { echo "BLOCKED: linked inventory directory" >&2; exit 2; }
  install -d -o root -g semaphore -m 0750 "$folder"
done
# Only the local runner identity is needed to execute the first catalog task.
cat > "$BASE/hosts.yml" <<'YAML'
---
all:
  children:
    site_runner:
      hosts:
        runner:
          ansible_connection: local
          ansible_python_interpreter: /opt/ansible/venv/bin/python
YAML
printf '%s\n' '---' 'site:' "  id: $SITE" "  owner_slug: $OWNER" "  type: $TYPE" > "$BASE/group_vars/all.yml"
chown root:semaphore "$BASE/hosts.yml" "$BASE/group_vars/all.yml"
chmod 0640 "$BASE/hosts.yml" "$BASE/group_vars/all.yml"

install -d -o root -g root -m 0755 /opt/digitalhouses/bootstrap /usr/local/sbin
install -o root -g root -m 0644 "$SRC/bootstrap/semaphore-project.py" /opt/digitalhouses/bootstrap/semaphore-project.py
python3 /opt/digitalhouses/bootstrap/semaphore-project.py \
  --site "$SITE" --project-title "$SITE" \
  --admin-password-file /root/dh-semaphore-initial-admin-password --apply
install -o root -g root -m 0700 "$SRC/bootstrap/semaphore-catalog-sync-request.sh" /usr/local/sbin/dh-semaphore-catalog-sync
install -o root -g root -m 0644 "$SRC/bootstrap/dh-semaphore-catalog-sync.service" /etc/systemd/system/dh-semaphore-catalog-sync.service
install -o root -g root -m 0644 "$SRC/bootstrap/dh-semaphore-catalog-sync.path" /etc/systemd/system/dh-semaphore-catalog-sync.path
systemctl daemon-reload
systemctl enable --now dh-semaphore-catalog-sync.path
[[ $(systemctl is-active dh-semaphore-catalog-sync.path) == active ]] || { echo "BLOCKED: task 00 watcher inactive" >&2; exit 2; }

# Prepare the fixed-purpose #40 installer during trusted root bootstrap.
# This installs only pinned assets and a request watcher; the Remote Commander
# package/service are NEVER installed until the operator explicitly runs task 40.
# Semaphore itself remains unprivileged and cannot select arbitrary root code.
REMOTE_BASE=/opt/digitalhouses/bootstrap/remote-commander
REMOTE_FILES=(
  playbooks/apply-runner-remote-commander.yml
  roles/runner_remote_commander/defaults/main.yml
  roles/runner_remote_commander/tasks/main.yml
  roles/runner_remote_commander/handlers/main.yml
  roles/runner_remote_commander/templates/digitalhouses-remote-commander.service.j2
)
for file in "${REMOTE_FILES[@]}"; do
  [[ -f "$SRC/$file" && ! -L "$SRC/$file" ]] || {
    echo "BLOCKED: pinned task 40 asset missing: $file" >&2; exit 2;
  }
done
for file in   bootstrap/remote-commander-install-request.sh   bootstrap/dh-remote-commander-install.service   bootstrap/dh-remote-commander-install.path; do
  [[ -f "$SRC/$file" && ! -L "$SRC/$file" ]] || {
    echo "BLOCKED: pinned task 40 worker missing: $file" >&2; exit 2;
  }
done
# Reject linked destination parents/files; an existing unknown worker is not adopted.
for path in "$REMOTE_BASE" "$REMOTE_BASE/playbooks" "$REMOTE_BASE/roles"   "$REMOTE_BASE/roles/runner_remote_commander"   "$REMOTE_BASE/roles/runner_remote_commander/defaults"   "$REMOTE_BASE/roles/runner_remote_commander/tasks"   "$REMOTE_BASE/roles/runner_remote_commander/handlers"   "$REMOTE_BASE/roles/runner_remote_commander/templates"   /usr/local/sbin/dh-remote-commander-install   /etc/systemd/system/dh-remote-commander-install.service   /etc/systemd/system/dh-remote-commander-install.path; do
  [[ ! -L "$path" ]] || { echo "BLOCKED: linked task 40 destination: $path" >&2; exit 2; }
done
[[ ! -e /usr/local/sbin/dh-remote-commander-install &&
   ! -e /etc/systemd/system/dh-remote-commander-install.service &&
   ! -e /etc/systemd/system/dh-remote-commander-install.path ]] || {
  echo "BLOCKED: pre-existing task 40 worker requires separate adoption" >&2; exit 2;
}
for file in "${REMOTE_FILES[@]}"; do
  install -D -o root -g root -m 0644 "$SRC/$file" "$REMOTE_BASE/$file"
done
install -o root -g root -m 0700   "$SRC/bootstrap/remote-commander-install-request.sh"   /usr/local/sbin/dh-remote-commander-install
for kind in service path; do
  install -o root -g root -m 0644     "$SRC/bootstrap/dh-remote-commander-install.$kind"     "/etc/systemd/system/dh-remote-commander-install.$kind"
done
systemctl daemon-reload
systemctl enable --now dh-remote-commander-install.path
[[ $(systemctl is-active dh-remote-commander-install.path) == active ]] || {
  echo "BLOCKED: task 40 watcher inactive" >&2; exit 2;
}
[[ $(systemctl is-enabled dh-remote-commander-install.path) == enabled ]] || {
  echo "BLOCKED: task 40 watcher not enabled" >&2; exit 2;
}
[[ $(systemctl is-active digitalhouses-remote-commander.service 2>/dev/null || true) != active ]] || {
  echo "BLOCKED: Remote Commander service unexpectedly active during bootstrap" >&2; exit 2;
}
echo "TASK_40_WATCHER=PASS REMOTE_AGENT_INSTALLED=NO"

# Independent API acceptance: exactly one template; no task has been launched.
python3 - "$SITE" <<'PY'
import pathlib
import runpy
import sys
module = runpy.run_path("/opt/digitalhouses/bootstrap/semaphore-project.py", run_name="dh_verify_task00")
api = module["API"]()
api.login(module["secure_file"](pathlib.Path("/root/dh-semaphore-initial-admin-password")))
projects = api.request("GET", "/projects")
matches = [p for p in projects if p.get("name") == sys.argv[1]]
assert len(matches) == 1, "Expected exactly one site project"
templates = api.request("GET", f"/project/{matches[0]['id']}/templates?sort=name&order=asc")
assert len(templates) == 1 and templates[0].get("name") == "00-Обновить список задач", "Expected only task 00"
assert templates[0].get("playbook") == "playbooks/request-semaphore-catalog-sync.yml", "Unexpected recovery playbook"
assert templates[0].get("autorun") in (None, False), "Task 00 autorun forbidden"
print("TASK_00_ONLY=PASS")
PY

echo "BOOTSTRAP_GUEST=PASS"
