#!/usr/bin/env bash
# One-time runner commissioning for the generic Semaphore NetAlertX watcher.
# Safe to re-run; never touches PVE guests, external networks, or application.
set -Eeuo pipefail
[[ "$(id -u)" == 0 ]] || { echo 'BLOCKED: root required' >&2; exit 2; }
[[ "$(hostname)" =~ ^ansible-[a-z][a-z0-9-]+$ ]] || { echo 'BLOCKED: runner identity' >&2; exit 2; }
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
install_managed() {
  local from=$1 to=$2 mode=$3
  if ! cmp -s "$from" "$to"; then
    install -o root -g root -m "$mode" "$from" "$to"
    echo "UPDATED=$to"
  else
    chown root:root "$to"
    chmod "$mode" "$to"
    echo "UNCHANGED=$to"
  fi
}
install_managed "$HERE/netalertx-deploy-request.sh" /usr/local/sbin/dh-netalertx-deploy 0750
install_managed "$HERE/dh-netalertx-deploy.service" /etc/systemd/system/dh-netalertx-deploy.service 0644
install_managed "$HERE/dh-netalertx-deploy.path" /etc/systemd/system/dh-netalertx-deploy.path 0644
systemctl daemon-reload
systemctl enable --now dh-netalertx-deploy.path
[[ "$(systemctl is-active dh-netalertx-deploy.path)" == active ]]
echo 'NETALERTX_WORKER_COMMISSIONED=PASS'
echo 'PVE_OR_GUEST_CHANGES=NONE'
