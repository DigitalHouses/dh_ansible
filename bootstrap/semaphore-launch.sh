#!/usr/bin/env bash
# Immutable launcher template; release builder pins both independent commits.
# Usage: bash dh-install.sh home
set -Eeuo pipefail
umask 077
SITE="$@"
REV=__RELEASE_COMMIT__
FLEET_REV=__FLEET_COMMIT__
GH_TARBALL=https://github.com/cli/cli/releases/download/v2.102.0/gh_2.102.0_linux_amd64.tar.gz
GH_SHA=bb766f710eef8ede859c18578c72c327597cd4c8a85b06001b1f3843c6019386
if (( $# != 1 )) || [[ ! "$SITE" =~ ^[a-z][a-z0-9_-]{0,30}$ || ! "$REV" =~ ^[0-9a-f]{40}$ || ! "$FLEET_REV" =~ ^[0-9a-f]{40}$ ]]; then
  echo "BLOCKED: invalid site or unpinned release" >&2; exit 2
fi
if (( EUID != 0 )) || [[ ! -f /etc/pve/user.cfg ]]; then
  echo "BLOCKED: root on PVE is required" >&2; exit 2
fi
TMP=$(mktemp -d /root/dh-semaphore-launch.XXXXXX)
trap 'rc=$?; rm -rf "$TMP"; echo "RESULT_RC=$rc"; echo "TERMINAL STAYS OPEN"' EXIT
mkdir -m 0700 "$TMP/github"

echo "=== DIGITALHOUSES · SEMAPHORE SINGLE WEB UI ==="
echo "CODE_RELEASE=$REV"
echo "FLEET_COMMIT=$FLEET_REV"
curl -fsSL --max-time 120 "$GH_TARBALL" -o "$TMP/gh.tgz"
printf '%s  %s\n' "$GH_SHA" "$TMP/gh.tgz" | sha256sum -c -
tar -tzf "$TMP/gh.tgz" | grep -x 'gh_2.102.0_linux_amd64/bin/gh' >/dev/null
tar -xzf "$TMP/gh.tgz" -C "$TMP" gh_2.102.0_linux_amd64/bin/gh
export PATH="$TMP/gh_2.102.0_linux_amd64/bin:$PATH"
export GH_CONFIG_DIR="$TMP/github"
export BROWSER=false
unset GH_TOKEN GITHUB_TOKEN || true

echo "=== OWNER · GITHUB DEVICE LOGIN ==="
echo "Open https://github.com/login/device on trusted phone/computer"
gh auth login --web --git-protocol https
gh auth status >/dev/null
gh api "repos/DigitalHouses/dh_ansible/contents/bootstrap/semaphore-host-install.py?ref=$REV" > "$TMP/file.json"
python3 - "$TMP/file.json" "$TMP/install.py" <<'PY'
import base64, hashlib, json, os, pathlib, sys
data=json.loads(pathlib.Path(sys.argv[1]).read_text())
if data.get("type") != "file" or data.get("encoding") != "base64":
    raise SystemExit("BLOCKED: unexpected release payload")
content=base64.b64decode(data["content"])
digest=hashlib.sha1(b"blob " + str(len(content)).encode() + b"\0" + content).hexdigest()
if digest != data.get("sha"):
    raise SystemExit("BLOCKED: release blob identity mismatch")
fd=os.open(sys.argv[2], os.O_EXCL|os.O_CREAT|os.O_WRONLY, 0o700)
with os.fdopen(fd,"wb") as out:
    out.write(content)
print("RELEASE_INSTALLER=PASS")
PY

PVE_ADDR=$(ip -4 -o addr show dev vmbr0 | awk '$3=="inet" {print $4}')
GATEWAY=$(ip -4 route show default | awk '$1=="default" && $2=="via" {print $3}')
RUNNER_CIDR=$(python3 - "$PVE_ADDR" "$GATEWAY" <<'PY'
import ipaddress, sys
try:
    interface=ipaddress.IPv4Interface(sys.argv[1])
    gw=ipaddress.IPv4Address(sys.argv[2])
    assert interface.network.prefixlen == 24
    assert gw in interface.network
    target=ipaddress.IPv4Address(int(interface.network.network_address)+231)
    assert target != interface.ip and target != gw
except (IndexError, ValueError, AssertionError):
    raise SystemExit("BLOCKED: ambiguous or non-standard PVE /24 network")
print(str(target)+"/24")
PY
)
PVE_IP=$(python3 - "$PVE_ADDR" <<'PY'
import ipaddress,sys
print(ipaddress.IPv4Interface(sys.argv[1]).ip)
PY
)
echo "PVE=$PVE_IP RUNNER=$RUNNER_CIDR GW=$GATEWAY"

python3 "$TMP/install.py" \
  --site "$SITE" \
  --runner-cidr "$RUNNER_CIDR" \
  --gateway "$GATEWAY" \
  --pve-ip "$PVE_IP" \
  --expected-commit "$REV" \
  --expected-fleet-commit "$FLEET_REV" \
  --use-github-session \
  --apply
echo "=== DONE ==="
