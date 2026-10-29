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
# The code version used by Semaphore must match the complete root-owned
# guest worker bundle. No VM action may start with a mixed/obsolete release.
UUID_RE='[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'
if [[ "$REQUEST_DATA" =~ ^MEDIA_ACCEPTANCE:($UUID_RE):([0-9a-f]{40})$ ]]; then
  ACTION="MEDIA_ACCEPTANCE"
  NONCE="${BASH_REMATCH[1]}"
  SOURCE_SHA="${BASH_REMATCH[2]}"
elif [[ "$REQUEST_DATA" =~ ^(MEDIA_DEPLOY_(CREATE|RECREATE|KEEP)_(IMPORT|ERASE)):($UUID_RE):([0-9a-f]{40})$ ]]; then
  ACTION="${BASH_REMATCH[1]}"
  NONCE="${BASH_REMATCH[4]}"
  SOURCE_SHA="${BASH_REMATCH[5]}"
elif [[ "$REQUEST_DATA" =~ ^(AUDIT|MEDIA_NFS_ATTACH|MEDIA_PLAN_(CREATE|RECREATE|KEEP)_(IMPORT|ERASE)):($UUID_RE)$ ]]; then
  ACTION="${BASH_REMATCH[1]}"
  NONCE="${BASH_REMATCH[4]}"
else
  exit 2
fi
rm -f -- "$REQUEST"
report() { printf '%s:%s\n' "$1" "$NONCE" > "$STATUS"; chmod 0644 "$STATUS"; }
MEDIA_RUNTIME_FILE=
on_exit() {
  rc=$?
  if [[ -n "$MEDIA_RUNTIME_FILE" && -f "$MEDIA_RUNTIME_FILE" ]]; then
    rm -f -- "$MEDIA_RUNTIME_FILE"
  fi
  if (( rc != 0 )); then report FAILED; fi
}
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
if [[ "$ACTION" == "MEDIA_ACCEPTANCE" ]]; then
  # Strictly read-only against the PVE host, guest, and all customer data.
  # Never run a Semaphore checkout as root; use only the pinned staged worker.
  /usr/bin/python3 /usr/local/sbin/dh-pve-media-release-verify \
    --expected-commit "$SOURCE_SHA" >> "$LOG" 2>&1
  MEDIA="$INV_ROOT/$SITE/media_vm.yml"
  PVE_FILE="$INV_ROOT/$SITE/pve.yml"
  ACCEPT_PLAY=/opt/digitalhouses/bootstrap/pve-access/playbooks/media-acceptance-worker.yml
  ACCEPT_SCRIPT=/opt/digitalhouses/bootstrap/pve-access/scripts/media-acceptance.py
  [[ -f "$MEDIA" && ! -L "$MEDIA" ]] || exit 2
  [[ -f "$PVE_FILE" && ! -L "$PVE_FILE" ]] || exit 2
  [[ "$(stat -c '%U:%G:%a' "$MEDIA")" == 'root:semaphore:640' ]] || exit 2
  [[ "$(stat -c '%U:%G:%a' "$ACCEPT_PLAY")" == 'root:root:644' ]] || exit 2
  [[ "$(stat -c '%U:%G:%a' "$ACCEPT_SCRIPT")" == 'root:root:644' ]] || exit 2
  /opt/ansible/venv/bin/ansible-playbook -i "$INV" "$ACCEPT_PLAY" \
    -e "media81_nonce=$NONCE" >> "$LOG" 2>&1
  report PASS
  exit 0
fi

if [[ "$ACTION" == MEDIA_DEPLOY_* ]]; then
  /usr/bin/python3 /usr/local/sbin/dh-pve-media-release-verify \
    --expected-commit "$SOURCE_SHA" >> "$LOG" 2>&1
  [[ "$ACTION" =~ ^MEDIA_DEPLOY_(CREATE|RECREATE|KEEP)_(IMPORT|ERASE)$ ]] || exit 2
  VM_ACTION="${BASH_REMATCH[1],,}"
  RAW_DISK_ACTION="${BASH_REMATCH[2],,}"
  PVE_FILE="$INV_ROOT/$SITE/pve.yml"
  [[ -f "$PVE_FILE" && ! -L "$PVE_FILE" ]] || exit 2
  PVE_IP="$(/opt/ansible/venv/bin/python -c 'import sys,yaml,ipaddress; d=yaml.safe_load(open(sys.argv[1]))["pve"]; host=d["host"]; ipaddress.IPv4Address(host); print(host)' "$PVE_FILE")"
  [[ "$PVE_IP" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]] || exit 2
  # Before any VM/PVE write, require site-scoped MQTT from existing
  # root-only PVE runtime. stdout is captured directly in a 0600 file.
  MEDIA_RUNTIME_FILE="$(mktemp /run/dh-media-runtime-XXXXXXXX.yml)"
  /usr/bin/ssh -T -o BatchMode=yes -o IdentitiesOnly=yes \
    -o StrictHostKeyChecking=yes \
    -o UserKnownHostsFile=/var/lib/ansible/.ssh/pve_host_audit_known_hosts \
    -o ConnectTimeout=8 \
    -i /var/lib/ansible/.ssh/pve_host_write_ed25519 \
    "ansible-linux@$PVE_IP" sudo -n /usr/local/sbin/dh-pve-media-runtime \
    --site "$SITE" > "$MEDIA_RUNTIME_FILE" 2>> "$LOG"
  [[ "$(stat -c '%U:%G:%a' "$MEDIA_RUNTIME_FILE")" == 'root:root:600' ]] || exit 2
  [[ -s "$MEDIA_RUNTIME_FILE" ]] || exit 2

  /usr/bin/ssh -T -o BatchMode=yes -o IdentitiesOnly=yes \
    -o StrictHostKeyChecking=yes \
    -o UserKnownHostsFile=/var/lib/ansible/.ssh/pve_host_audit_known_hosts \
    -o ConnectTimeout=8 \
    -i /var/lib/ansible/.ssh/pve_host_write_ed25519 \
    "ansible-linux@$PVE_IP" sudo -n /usr/local/sbin/dh-pve-media-deploy \
    --site "$SITE" --vm-action "$VM_ACTION" \
    --raw-disk-action "$RAW_DISK_ACTION" --apply >> "$LOG" 2>&1
  if grep -Fxq "VM_CREATED=YES" "$LOG" || \
     grep -Fxq "VM_RECREATE=REMOVED_VERIFIED_LEGACY" "$LOG" || \
     grep -Fxq "VM_RECREATE=REMOVED_VERIFIED_MANAGED" "$LOG"; then
    # A newly created Debian VM has a different host key. Never reset
    # an existing guest's SSH trust for an ordinary reconcile run.
    GUEST_IP="$(/opt/ansible/venv/bin/python -c 'import yaml,ipaddress,sys; x=yaml.safe_load(open(sys.argv[1]))["media_vm"]["network"]["ipv4"]; print(ipaddress.IPv4Interface(x).ip)' "$INV_ROOT/$SITE/media_vm.yml")"
    if [[ -f /root/.ssh/known_hosts ]]; then
      /usr/bin/ssh-keygen -R "$GUEST_IP" -f /root/.ssh/known_hosts >> "$LOG" 2>&1
    fi
    echo "VM_SSH_IDENTITY=REINITIALIZED_AFTER_VERIFIED_VM_CREATION" >> "$LOG"
  fi
  GUEST_PLAY=/opt/digitalhouses/bootstrap/pve-access/playbooks/setup-pve-media-guest.yml
  [[ -f "$GUEST_PLAY" && ! -L "$GUEST_PLAY" ]] || exit 2
  [[ "$(stat -c '%U:%G:%a' "$GUEST_PLAY")" == 'root:root:644' ]] || exit 2
  export ANSIBLE_ROLES_PATH=/opt/digitalhouses/bootstrap/pve-access/roles
  /opt/ansible/venv/bin/ansible-playbook -i "$INV" "$GUEST_PLAY" \
    -e "media_vm_action=$VM_ACTION" \
    -e "media_raw_disk_action=$RAW_DISK_ACTION" \
    -e "dh_media_runtime_secret_file=$MEDIA_RUNTIME_FILE" >> "$LOG" 2>&1
  # Backup target migration is a separate approved operation AFTER
  # media application acceptance. Never modify PVE backup jobs or storage
  # merely because the guest NFS export was created successfully.
  echo "MEDIA_BACKUP_MIGRATION=DEFERRED_UNTIL_ACCEPTANCE" >> "$LOG"
  report PASS
  exit 0
fi
if [[ "$ACTION" == "MEDIA_NFS_ATTACH" ]]; then
  NFS_PLAY=/opt/digitalhouses/bootstrap/pve-access/playbooks/attach-pve-media-backup-nfs.yml
  [[ -f "$NFS_PLAY" && ! -L "$NFS_PLAY" ]] || exit 2
  [[ "$(stat -c '%U:%G:%a' "$NFS_PLAY")" == 'root:root:644' ]] || exit 2
  /opt/ansible/venv/bin/ansible-playbook -i "$INV" "$NFS_PLAY" >> "$LOG" 2>&1
  report PASS
  exit 0
fi
if [[ "$ACTION" == MEDIA_PLAN_* ]]; then
  [[ "$ACTION" =~ ^MEDIA_PLAN_(CREATE|RECREATE|KEEP)_(IMPORT|ERASE)$ ]] || exit 2
  MEDIA_PLAN_VM_ACTION="${BASH_REMATCH[1],,}"
  MEDIA_PLAN_DISK_ACTION="${BASH_REMATCH[2],,}"
fi
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
    -e "pve_media_plan_nonce=$NONCE" \
    -e "media_vm_action=$MEDIA_PLAN_VM_ACTION" \
    -e "media_raw_disk_action=$MEDIA_PLAN_DISK_ACTION" >> "$LOG" 2>&1
  [[ -f /run/dh-pve-media-plan.json && ! -L /run/dh-pve-media-plan.json ]] || exit 2
  [[ "$(stat -c '%U:%G:%a' /run/dh-pve-media-plan.json)" == 'root:semaphore:640' ]] || exit 2
fi
report PASS
