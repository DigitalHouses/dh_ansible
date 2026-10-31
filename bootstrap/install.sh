#!/usr/bin/env bash
# DigitalHouses tiny fresh Bootstrap: LXC -> Git -> Ansible/Semaphore -> task 00.
set -Eeuo pipefail
umask 077

# Immutable public code revision; the standard installation command pins this file.
CODE_RELEASE="e89d9043ba88607a077e7636d9b0890e65f96811"
CTID=1231
GIT_URL="https://github.com/DigitalHouses/dh_ansible.git"
[[ $# -eq 0 && $EUID -eq 0 && -f /etc/pve/user.cfg ]] || {
  echo "BLOCKED: run without arguments as root on Proxmox VE" >&2; exit 2;
}
read -r -p "Site ID (<owner>-<type>): " SITE
SITE="${SITE%$'\r'}"
[[ ${#SITE} -le 31 && $SITE =~ ^[a-z][a-z0-9]*(-[a-z0-9]+)+$ ]] || {
  echo "BLOCKED: invalid Site ID" >&2; exit 2;
}
read -r -p "Confirm site $SITE (y/n): " YES
[[ $YES == y || $YES == Y ]] || { echo "BLOCKED: site not confirmed" >&2; exit 2; }

LOG_DIR=/var/log/digitalhouses/ansible
install -d -o root -g root -m 0700 "$LOG_DIR"
LOG="$(mktemp "$LOG_DIR/tiny-$(date -u +%Y%m%dT%H%M%SZ).XXXXXXXX.log")"
chmod 0600 "$LOG"
MOUNTED=0
on_exit() {
  local rc=$?
  trap - EXIT
  if (( MOUNTED )); then pct unmount "$CTID" || true; fi
  echo "RESULT_RC=$rc"
  echo "LOG_FILE=$LOG"
  echo "TERMINAL STAYS OPEN"
}
trap on_exit EXIT
exec > >(tee -a "$LOG") 2>&1

HOST_CIDR="$(ip -4 -o addr show dev vmbr0 | awk '$3 == "inet" {print $4}')"
HOST_GW="$(ip -4 route show default | awk '$1 == "default" && $2 == "via" {print $3}')"
read -r RUNNER_CIDR GATEWAY < <(python3 - "$HOST_CIDR" "$HOST_GW" <<'PY'
import ipaddress, sys
iface = ipaddress.IPv4Interface(sys.argv[1])
gw = ipaddress.IPv4Address(sys.argv[2])
assert iface.network.prefixlen == 24 and gw in iface.network
runner = ipaddress.IPv4Address(int(iface.network.network_address) + 231)
assert runner not in (iface.ip, gw)
print(f"{runner}/24 {gw}")
PY
)
RUNNER_IP="${RUNNER_CIDR%/*}"
echo "SITE=$SITE PVE=$HOST_CIDR RUNNER=$RUNNER_CIDR CODE=$CODE_RELEASE"
# An existing CT/VM is never reused, overwritten or destroyed by Bootstrap.
if pct config "$CTID" >/dev/null 2>&1 || qm config "$CTID" >/dev/null 2>&1; then
  echo "BLOCKED: guest ID $CTID already exists; fresh Bootstrap only" >&2; exit 2
fi

echo "=== 1/4 · DEBIAN 13 LXC ==="
pveam update
TEMPLATE="$(pveam available --section system |
  awk '$2 ~ /^debian-13-standard_[[:alnum:].+-]+_amd64\.tar\.zst$/ {print $2}' |
  sort -V | tail -n1)"
[[ -n $TEMPLATE ]] || { echo "BLOCKED: official Debian 13 template unavailable" >&2; exit 2; }
if ! pveam list local | grep -Fq "local:vztmpl/$TEMPLATE"; then
  pveam download local "$TEMPLATE"
fi
# PVE unprivileged rootfs extraction needs an accessible root directory;
# restrict the relaxed umask to this single standard pct create invocation.
(umask 022; pct create "$CTID" "local:vztmpl/$TEMPLATE" \
  --hostname "ansible-$SITE" --ostype debian \
  --cores 1 --memory 1024 --swap 512 --rootfs local-lvm:16 \
  --unprivileged 1 --features nesting=1 \
  --net0 "name=eth0,bridge=vmbr0,ip=$RUNNER_CIDR,gw=$GATEWAY" \
  --onboot 0 --protection 1 --start 0)
# The Debian 13 template may ship /etc mode 0700, which breaks guest D-Bus.
pct mount "$CTID"
MOUNTED=1
ETC="/var/lib/lxc/$CTID/rootfs/etc"
[[ -d $ETC && ! -L $ETC ]] || { echo "BLOCKED: untrusted guest /etc" >&2; exit 2; }
case "$(stat -c %a "$ETC")" in
  700) chmod 0755 "$ETC" ;;
  755) ;;
  *) echo "BLOCKED: unexpected guest /etc mode" >&2; exit 2 ;;
esac
pct unmount "$CTID"
MOUNTED=0
pct start "$CTID"
[[ $(pct exec "$CTID" -- hostname) == "ansible-$SITE" ]] || {
  echo "BLOCKED: container hostname mismatch" >&2; exit 2;
}
echo "CT_CREATE=PASS"

echo "=== 2/4 · PINNED GITHUB CHECKOUT ==="
pct exec "$CTID" -- apt-get update -o APT::Update::Error-Mode=any
pct exec "$CTID" -- env DEBIAN_FRONTEND=noninteractive apt-get install -y \
  --no-install-recommends git python3 iproute2 ca-certificates
pct exec "$CTID" -- install -d -o root -g root -m 0700 /opt/digitalhouses
pct exec "$CTID" -- git clone -q "$GIT_URL" /opt/digitalhouses/src
pct exec "$CTID" -- git -C /opt/digitalhouses/src checkout -q --detach "$CODE_RELEASE"
ACTUAL="$(pct exec "$CTID" -- git -C /opt/digitalhouses/src rev-parse HEAD)"
[[ $ACTUAL == "$CODE_RELEASE" ]] || { echo "BLOCKED: Git revision mismatch" >&2; exit 2; }
echo "CODE_GIT_SHA=PASS $ACTUAL"

echo "=== 3/4 · ANSIBLE + SEMAPHORE ==="
pct exec "$CTID" -- python3 /opt/digitalhouses/src/bootstrap/semaphore-guest-install.py \
  --site "$SITE" --runner-ip "$RUNNER_IP" \
  --requirements /opt/digitalhouses/src/requirements/runner.txt --apply
echo "SEMAPHORE_INSTALL=PASS"

echo "=== 4/4 · SINGLE TASK 00 ==="
pct exec "$CTID" -- bash /opt/digitalhouses/src/bootstrap/task-00-setup.sh "$SITE" "$CODE_RELEASE"
echo "BOOTSTRAP_FRESH_DONE=PASS"
echo "SEMAPHORE_URL=https://$RUNNER_IP:3000/"
echo "ADMIN_PASSWORD_FILE=CT$CTID:/root/dh-semaphore-initial-admin-password"
echo "ONLY_TASK=00-Обновить список задач"
echo "GET_SEMAPHORE_ADMIN_PASSWORD: pct exec $CTID -- cat /root/dh-semaphore-initial-admin-password"
