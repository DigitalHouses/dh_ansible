#!/usr/bin/env bash
# Generic guarded replacement of disposable site runner CT1231 on actual PVE.
# Args: expected-old-site new-site pinned-bootstrap-revision expected-PVE-IP
set -Eeuo pipefail
umask 077
[[ $# == 4 ]] || { echo "BLOCKED: old-site new-site bootstrap-sha expected-PVE-IP required" >&2; exit 2; }
OLD_SITE="$1"
NEW_SITE="$2"
PIN="$3"
EXPECTED_PVE_IP="$4"
CTID=1231
[[ $EUID == 0 && -f /etc/pve/user.cfg ]] || { echo "BLOCKED: PVE root required" >&2; exit 2; }
[[ $OLD_SITE =~ ^[a-z][a-z0-9]*(-[a-z0-9]+)+$ ]] || { echo "BLOCKED: invalid old site" >&2; exit 2; }
[[ $NEW_SITE =~ ^[a-z][a-z0-9]*(-[a-z0-9]+)+$ && $NEW_SITE != "$OLD_SITE" ]] || {
  echo "BLOCKED: new site must differ" >&2; exit 2;
}
[[ $PIN =~ ^[0-9a-f]{40}$ ]] || { echo "BLOCKED: invalid pinned SHA" >&2; exit 2; }
CIDR="$(ip -4 -o addr show dev vmbr0 | awk '$3=="inet"{print $4}')"
[[ $(wc -w <<< "$CIDR") == 1 ]] || { echo "BLOCKED: vmbr0 PVE IP not unique" >&2; exit 2; }
[[ $(cut -d/ -f1 <<< "$CIDR") == "$EXPECTED_PVE_IP" ]] || {
  echo "BLOCKED: wrong PVE host/IP; nothing will be destroyed" >&2; exit 2;
}
RUNNER_IP="$(python3 - "$CIDR" <<'PY'
import ipaddress,sys
iface=ipaddress.IPv4Interface(sys.argv[1])
assert iface.network.prefixlen==24
print(ipaddress.IPv4Address(int(iface.network.network_address)+231))
PY
)"
[[ -n "$RUNNER_IP" ]] || exit 2
if [[ -f /etc/pve/qemu-server/2253.conf ]]; then
  qm config 2253 >/dev/null
  echo "PRESERVE_VM2253=PASS"
fi
if qm config "$CTID" >/dev/null 2>&1; then
  echo "BLOCKED: ID1231 is a QEMU VM" >&2; exit 2
fi
STAGING="$(mktemp -d /root/dh-runner-fresh.XXXXXXXX)"
cleanup() {
  local rc=$?
  trap - EXIT
  rm -rf "$STAGING"
  echo "RESULT_RC=$rc"
  echo "TERMINAL STAYS OPEN"
}
trap cleanup EXIT
curl -fLsS --connect-timeout 10 --retry 2 --max-time 90 \
  "https://raw.githubusercontent.com/DigitalHouses/dh_ansible/$PIN/bootstrap/install.sh" \
  -o "$STAGING/install.sh"
bash -n "$STAGING/install.sh"
grep -Fq 'CTID=1231' "$STAGING/install.sh" || { echo "BLOCKED: different bootstrap CTID" >&2; exit 2; }
# Ensure the required official guest image is locally available before we
# destroy the old runner; otherwise an unrelated image download failure
# could strand the site without a runner.
pveam update
TEMPLATE="$(pveam available --section system | awk '$2 ~ /^debian-13-standard_[[:alnum:].+-]+_amd64[.]tar[.]zst$/ {print $2}' | sort -V | tail -n1)"
[[ -n "$TEMPLATE" ]] || { echo "BLOCKED: Debian 13 template unavailable" >&2; exit 2; }
if ! pveam list local | grep -Fq "local:vztmpl/$TEMPLATE"; then
  pveam download local "$TEMPLATE"
fi
echo "TEMPLATE_READY=PASS"

if pct config "$CTID" >/dev/null 2>&1; then
  CONF="$(pct config "$CTID")"
  grep -Fxq "hostname: ansible-$OLD_SITE" <<< "$CONF" || {
    echo "BLOCKED: unexpected CT1231 hostname" >&2; exit 2;
  }
  grep -Fxq 'unprivileged: 1' <<< "$CONF" || {
    echo "BLOCKED: CT1231 must be an unprivileged runner" >&2; exit 2;
  }
  grep -Eq "^net0: .*ip=$RUNNER_IP/24([,]|$)" <<< "$CONF" || {
    echo "BLOCKED: CT1231 IP mismatch" >&2; exit 2;
  }
  grep -Eq '^rootfs: local-lvm:(vm|subvol)-1231-disk-[[:digit:]]+,size=16G([,]|$)' <<< "$CONF" || {
    echo "BLOCKED: CT1231 rootfs not verified" >&2; exit 2;
  }
  if grep -Eq '^(mp[[:digit:]]+|hookscript|unused[[:digit:]]+|lxc\.mount\.entry):' <<< "$CONF"; then
    echo "BLOCKED: external mount, hook or orphan disk on CT1231" >&2; exit 2
  fi
  echo "CT1231_IDENTITY=PASS"
  echo "OLD_SITE=$OLD_SITE NEW_SITE=$NEW_SITE"
  if grep -Fxq 'protection: 1' <<< "$CONF"; then pct set "$CTID" --protection 0; fi
  if pct status "$CTID" | grep -Fq running; then pct stop "$CTID" --timeout 60; fi
  pct destroy "$CTID"
  echo "OLD_RUNNER_REPLACED=PASS"
else
  echo "CT1231_ABSENT=PASS"
fi
printf '%s\n' "$NEW_SITE" y | bash "$STAGING/install.sh"
[[ $(pct exec "$CTID" -- hostname) == "ansible-$NEW_SITE" ]] || {
  echo "BLOCKED: new site hostname wrong" >&2; exit 2;
}
pct exec "$CTID" -- ip -4 -o addr show dev eth0 | grep -Fq "$RUNNER_IP/24" || {
  echo "BLOCKED: new runner IP mismatch" >&2; exit 2;
}
pct exec "$CTID" -- test -f "/var/lib/ansible/site-data/$NEW_SITE/pve-runtime.yml"
echo "FRESH_RUNNER_SITE=$NEW_SITE"
echo "FRESH_RUNNER=PASS"
echo "SEMAPHORE=https://$RUNNER_IP:3000/"
echo "NEXT=Run 00, authorize GitHub in 05, enter Fleet Vault password in 06"
echo "DONE"
