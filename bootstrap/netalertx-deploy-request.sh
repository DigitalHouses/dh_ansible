#!/usr/bin/env bash
# Fixed-purpose root-owned worker: one trusted site, one declarative playbook.
# Semaphore may submit ONLY DEPLOY:<uuid>:<site>; never a path or shell command.
set -Eeuo pipefail
umask 077
PATH=/opt/ansible/venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export PATH
REQ=/var/lib/semaphore/dh-netalertx-deploy.request
STATUS=/run/dh-netalertx-deploy.status
LOG=/var/log/dh-netalertx-deploy.log
LOCK=/run/dh-netalertx-deploy.lock
SITE=''
NONCE=''
RESULT='FAILED'
WORK=''

report() {
    local out
    out=$(mktemp /run/dh-netalertx-deploy.status.XXXXXX)
    printf '%s|%s\n' "$NONCE" "$RESULT" > "$out"
    chown root:semaphore "$out"
    chmod 0640 "$out"
    mv -f "$out" "$STATUS"
}
finish() {
    local rc=$?
    set +e
    if [[ -n "$NONCE" ]]; then report; fi
    if [[ -n "$WORK" ]]; then rm -rf -- "$WORK"; fi
    rm -f -- "$REQ"
    return "$rc"
}
trap finish EXIT
exec 9>"$LOCK"
flock -n 9 || { echo 'Another NetAlertX deployment is running' >&2; exit 1; }
[[ -f "$REQ" ]] || exit 0
[[ "$(stat -c %U "$REQ")" == semaphore ]] || exit 2
[[ "$(stat -c %a "$REQ")" == 600 ]] || exit 2
request=$(cat "$REQ")
if [[ "$request" =~ ^DEPLOY:([a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}):([a-z][a-z0-9-]*)$ ]]; then
    NONCE="${BASH_REMATCH[1]}"
    SITE="${BASH_REMATCH[2]}"
else
    exit 2
fi
[[ "$(hostname)" == "ansible-$SITE" ]] || exit 2
SITE_DIR="/var/lib/ansible/site-data/$SITE"
[[ -r "$SITE_DIR/hosts.yml" && -r "$SITE_DIR/netalertx.yml" ]] || exit 2
[[ -r /var/lib/ansible/.ssh/pve_host_write_ed25519 ]] || exit 2
RESULT='RUNNING'
report
exec 1>>"$LOG" 2>&1
echo "REQUEST_START site=$SITE request=$NONCE at=$(date -Is)"
WORK=$(mktemp -d /var/lib/ansible/netalertx-release.XXXXXX)
chmod 0700 "$WORK"
git -c core.hooksPath=/dev/null clone --quiet --depth=1 --branch main --single-branch https://github.com/DigitalHouses/dh_ansible.git "$WORK/repo"
cd "$WORK/repo"
SHA=$(git rev-parse HEAD)
echo "SOURCE_SHA=$SHA"
# Only the hardcoded worker entrypoint is executed as root; no request fields
# can alter the playbook path, repo, argument list, key or PVE VMID.
env -i \
    HOME=/root \
    PATH="$PATH" \
    LANG=C.UTF-8 \
    /opt/ansible/venv/bin/ansible-playbook \
      -i "$SITE_DIR/hosts.yml" \
      playbooks/deploy-netalertx-pve-from-worker.yml \
      -e netalertx_pve_worker_approved=true
echo "REQUEST_VERIFIED site=$SITE request=$NONCE source=$SHA"
RESULT='PASS'
