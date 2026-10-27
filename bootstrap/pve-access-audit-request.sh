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
[[ "$REQUEST_DATA" =~ ^(AUDIT|MEDIA_PLAN|MEDIA_DEPLOY_(CREATE|IMPORT)_(KEEP|RECREATE)_(PRESERVE|ERASE)):([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$ ]] || exit 2
ACTION="${BASH_REMATCH[1]}"
NONCE="${BASH_REMATCH[5]}"
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
if [[ "$ACTION" == MEDIA_DEPLOY_* ]]; then
  [[ "$ACTION" =~ ^MEDIA_DEPLOY_(CREATE|IMPORT)_(KEEP|RECREATE)_(PRESERVE|ERASE)$ ]] || exit 2
  STORAGE_ACTION="${BASH_REMATCH[1],,}"
  VM_POLICY="${BASH_REMATCH[2],,}"
  DISK_POLICY="${BASH_REMATCH[3],,}"
  [[ "$STORAGE_ACTION" != import || "$DISK_POLICY" == preserve ]] || exit 2
  PVE_FILE="$INV_ROOT/$SITE/pve.yml"
  [[ -f "$PVE_FILE" && ! -L "$PVE_FILE" ]] || exit 2
  PVE_IP="$(/opt/ansible/venv/bin/python -c 'import sys,yaml,ipaddress; d=yaml.safe_load(open(sys.argv[1]))["pve"]; host=d["host"]; ipaddress.IPv4Address(host); print(host)' "$PVE_FILE")"
  [[ "$PVE_IP" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]] || exit 2
  /usr/bin/ssh -T -o BatchMode=yes -o IdentitiesOnly=yes \
    -o StrictHostKeyChecking=yes \
    -o UserKnownHostsFile=/var/lib/ansible/.ssh/pve_host_audit_known_hosts \
    -o ConnectTimeout=8 \
    -i /var/lib/ansible/.ssh/pve_host_write_ed25519 \
    "ansible-linux@$PVE_IP" sudo -n /usr/local/sbin/dh-pve-media-deploy \
    --site "$SITE" --storage-action "$STORAGE_ACTION" \
    --vm-policy "$VM_POLICY" --disk-policy "$DISK_POLICY" --apply >> "$LOG" 2>&1
  if grep -Fxq "VM_RECREATE=REMOVED_VERIFIED_LEGACY" "$LOG" || \
     grep -Fxq "VM_RECREATE=REMOVED_VERIFIED_MANAGED" "$LOG"; then
    # The newly provisioned Debian VM generates a new host key.
    GUEST_IP="$(/opt/ansible/venv/bin/python -c 'import yaml,ipaddress,sys; x=yaml.safe_load(open(sys.argv[1]))["media_vm"]["network"]["ipv4"]; print(ipaddress.IPv4Interface(x).ip)' "$INV_ROOT/$SITE/media_vm.yml")"
    if [[ -f /root/.ssh/known_hosts ]]; then
      /usr/bin/ssh-keygen -R "$GUEST_IP" -f /root/.ssh/known_hosts >> "$LOG" 2>&1
    fi
    echo "VM_SSH_IDENTITY=REINITIALIZED_AFTER_APPROVED_RECREATE" >> "$LOG"
  fi
  GUEST_PLAY=/opt/digitalhouses/bootstrap/pve-access/playbooks/setup-pve-media-guest.yml
  [[ -f "$GUEST_PLAY" && ! -L "$GUEST_PLAY" ]] || exit 2
  [[ "$(stat -c '%U:%G:%a' "$GUEST_PLAY")" == 'root:root:644' ]] || exit 2
  export ANSIBLE_ROLES_PATH=/opt/digitalhouses/bootstrap/pve-access/roles
  /opt/ansible/venv/bin/ansible-playbook -i "$INV" "$GUEST_PLAY" \
    -e "media_storage_action=$STORAGE_ACTION" \
    -e "media_vm_policy=$VM_POLICY" \
    -e "media_disk_policy=$DISK_POLICY" >> "$LOG" 2>&1
  report PASS
  exit 0
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
    -e "pve_media_plan_nonce=$NONCE" >> "$LOG" 2>&1
  [[ -f /run/dh-pve-media-plan.json && ! -L /run/dh-pve-media-plan.json ]] || exit 2
  [[ "$(stat -c '%U:%G:%a' /run/dh-pve-media-plan.json)" == 'root:semaphore:640' ]] || exit 2
fi
report PASS
