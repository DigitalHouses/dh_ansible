#!/usr/bin/env bash
# In-place, version-pinned Semaphore inventory repair on the existing runner.
# Run from a pinned Git checkout inside the runner CT. Never recreates guests.
set -Eeuo pipefail
umask 077

[[ $# -eq 1 && $1 =~ ^[0-9a-f]{40}$ ]] || {
  echo 'BLOCKED: expected pinned Git commit SHA' >&2; exit 2;
}
RELEASE="$1"
[[ $EUID -eq 0 && ! -f /etc/pve/user.cfg ]] || {
  echo 'BLOCKED: existing runner LXC root required' >&2; exit 2;
}
SOURCE="$(cd -- "$(dirname -- "$0")/.." && pwd -P)"
[[ -f "$SOURCE/bootstrap/semaphore-project.py" && ! -L "$SOURCE/bootstrap/semaphore-project.py" ]] || {
  echo 'BLOCKED: missing seeder in pinned checkout' >&2; exit 2;
}
ACTUAL="$(git -C "$SOURCE" rev-parse HEAD)"
[[ $ACTUAL == "$RELEASE" && -z $(git -C "$SOURCE" status --porcelain) ]] || {
  echo 'BLOCKED: checkout is not the pinned clean revision' >&2; exit 2;
}
HOST="$(hostname)"
[[ $HOST =~ ^ansible-[a-z][a-z0-9]*(-[a-z0-9]+)+$ ]] || {
  echo 'BLOCKED: unexpected runner hostname' >&2; exit 2;
}
SITE="$(printf %s "$HOST" | sed 's/^ansible-//')"
DATA="/var/lib/ansible/site-data/$SITE"
for item in /var/lib/ansible/site-data "$DATA" "$DATA/hosts.yml" "$DATA/group_vars/all.yml"; do
  [[ -e $item && ! -L $item ]] || {
    echo 'BLOCKED: missing or linked private site inventory' >&2; exit 2;
  }
done
[[ $(stat -c '%U:%G:%a' "$DATA/hosts.yml") == root:semaphore:640 ]] || {
  echo 'BLOCKED: site inventory ownership/mode drift' >&2; exit 2;
}
PINNED=/opt/digitalhouses/bootstrap/semaphore-project.py
[[ -f "$PINNED" && ! -L "$PINNED" && $(stat -c '%U:%G:%a' "$PINNED") == root:root:644 ]] || {
  echo 'BLOCKED: existing protected seeder drift' >&2; exit 2;
}
TOKEN=/root/dh-semaphore-catalog-api-token
[[ -f "$TOKEN" && ! -L "$TOKEN" && $(stat -c '%U:%G:%a' "$TOKEN") == root:root:600 ]] || {
  echo 'BLOCKED: local API credential unavailable' >&2; exit 2;
}

python3 -m py_compile "$SOURCE/bootstrap/semaphore-project.py"

# Apply only controlled inventory/repository migration plus existing Task 00.
# Preserve inventory ID, templates, their history, and all credential objects.
python3 "$SOURCE/bootstrap/semaphore-project.py" \
  --site "$SITE" --project-title "$SITE" \
  --admin-token-file "$TOKEN" --apply

# Persist the fixed implementation for subsequent root-owned Task 00 runs.
install -o root -g root -m 0644 "$SOURCE/bootstrap/semaphore-project.py" "$PINNED"

# Confirm repeat invocation does not introduce drift.
python3 "$PINNED" \
  --site "$SITE" --project-title "$SITE" \
  --admin-token-file "$TOKEN" --apply

runuser -u semaphore -- /opt/ansible/venv/bin/ansible-inventory \
  -i "$DATA/hosts.yml" --list |
  /opt/ansible/venv/bin/python -c 'import json,sys; d=json.load(sys.stdin); assert d["site_runner"]["hosts"] == ["runner"], "SITE_RUNNER_NOT_LOADED"'

echo "SEMAPHORE_SITE_INVENTORY_UPGRADE=PASS"
echo "SOURCE_SHA=$RELEASE"
echo "NEXT=RUN_TASK_00"
