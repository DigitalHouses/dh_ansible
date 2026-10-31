#!/usr/bin/env bash
# Guest-only handoff: local runner inventory, single Semaphore task 00 and its watcher.
set -Eeuo pipefail
umask 077

SITE="${1:?site ID required}"
SHA="${2:?pinned Git revision required}"
PVE_IPV4="${3:?actual PVE IPv4 from host vmbr0 required}"
SRC=/opt/digitalhouses/src
BASE="/var/lib/ansible/site-data/$SITE"
[[ $EUID -eq 0 && ! -f /etc/pve/user.cfg ]] || { echo "BLOCKED: guest root required" >&2; exit 2; }
[[ $SITE =~ ^[a-z][a-z0-9]*(-[a-z0-9]+)+$ ]] || { echo "BLOCKED: invalid site" >&2; exit 2; }
[[ $SHA =~ ^[0-9a-f]{40}$ && $(hostname) == "ansible-$SITE" ]] || { echo "BLOCKED: guest/revision mismatch" >&2; exit 2; }
[[ $(git -C "$SRC" rev-parse HEAD) == "$SHA" ]] || { echo "BLOCKED: Git commit mismatch" >&2; exit 2; }
[[ -f /root/dh-semaphore-initial-admin-password ]] || { echo "BLOCKED: Semaphore not installed" >&2; exit 2; }
# The PVE address is a fact of the Bootstrap host, never a number inferred
# from CTID/VMID or a permanent per-site value in Git.
python3 - "$PVE_IPV4" <<'PY'
import ipaddress
import sys
address = ipaddress.IPv4Address(sys.argv[1])
assert not (address.is_unspecified or address.is_loopback or address.is_multicast or address.is_link_local)
PY

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
# Local bootstrap observation, not a Fleet declaration. Reverify it against
# PVE's actual identity before privileged operations after any IP change.
printf '%s\n' '---' 'pve_observed:' "  ipv4: $PVE_IPV4" '  source: bootstrap_vmbr0' > "$BASE/pve-runtime.yml"
chown root:semaphore "$BASE/pve-runtime.yml"
chmod 0640 "$BASE/pve-runtime.yml"

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
