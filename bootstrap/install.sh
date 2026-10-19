#!/usr/bin/env bash
# Single-repository public PVE entrypoint: immutable dh_ansible code + private Fleet Detail data.
set -Eeuo pipefail
umask 077

SITE=""
if (( $# > 0 )) && [[ "${1}" != --* ]]; then
  SITE="$1"
  shift
fi
CODE_RELEASE="52104927367c4c9d26a0d1588a3959096d7be9a2"
CODE_INSTALLER_BLOB="c5ef0ac433f1be530c4e0959ecb757865062e26d"
FLEET_RELEASE="6dd57b2e35573a4883e70188590e8e595688bd71"
GH_VERSION="2.102.0"
GH_SHA256="bb766f710eef8ede859c18578c72c327597cd4c8a85b06001b1f3843c6019386"

RESUME_ARGS=()
if (( $# == 1 )); then
  case "$1" in
    --resume|--resume-fresh|--resume-ansible|--resume-semaphore) RESUME_ARGS=("$1") ;;
    *) echo "BLOCKED: unknown operation" >&2; exit 2 ;;
  esac
elif (( $# != 0 )); then
  echo "BLOCKED: usage: install.sh [<owner>-<type>] [--resume|--resume-fresh|--resume-ansible|--resume-semaphore]" >&2
  exit 2
fi
if [[ -z "$SITE" ]]; then
  if ! IFS= read -r -p "Введите slug объекта (<owner>-<type>): " SITE; then
    echo "BLOCKED: site slug input missing" >&2; exit 2
  fi
fi
if [[ ! "$SITE" =~ ^[a-z][a-z0-9]*(-[a-z0-9]+)+$ ]]; then
  echo "BLOCKED: invalid site ID" >&2
  exit 2
fi
if (( EUID != 0 )) || [[ ! -f /etc/pve/user.cfg ]]; then
  echo "BLOCKED: root terminal on Proxmox VE required" >&2
  exit 2
fi

echo "=== CANONICAL SITE CONFIRMATION ==="
echo "OBJECT_SLUG=$SITE"
echo "DOWNSTREAM_SITE=$SITE"
if ! IFS= read -r -p "Подтвердить объект '$SITE' для всех этапов (y/n): " CONFIRM_SITE; then
  echo "BLOCKED: confirmation missing; no actions performed" >&2; exit 2
fi
case "$CONFIRM_SITE" in
  y|Y) echo "SITE_CONFIRMATION=YES $SITE" ;;
  n|N) echo "CANCELLED: site not confirmed; no actions performed"; exit 0 ;;
  *) echo "BLOCKED: answer must be y or n" >&2; exit 2 ;;
esac

WORK=$(mktemp -d /root/ansimble-network.XXXXXX)
trap 'rc=$?; rm -rf "$WORK"; echo "RESULT_RC=$rc"; echo "TERMINAL STAYS OPEN"' EXIT
mkdir -m 700 "$WORK/gh-config"

echo "=== DIGITALHOUSES · PUBLIC NETWORK BOOTSTRAP ==="
echo "CODE_RELEASE=$CODE_RELEASE"
echo "FLEET_RELEASE=$FLEET_RELEASE"
curl -fsSL --max-time 120 \
  "https://github.com/cli/cli/releases/download/v${GH_VERSION}/gh_${GH_VERSION}_linux_amd64.tar.gz" \
  -o "$WORK/gh.tgz"
printf '%s  %s\n' "$GH_SHA256" "$WORK/gh.tgz" | sha256sum -c -
tar -xzf "$WORK/gh.tgz" -C "$WORK" "gh_${GH_VERSION}_linux_amd64/bin/gh"
export PATH="$WORK/gh_${GH_VERSION}_linux_amd64/bin:$PATH"
export GH_CONFIG_DIR="$WORK/gh-config"
export BROWSER=false
unset GH_TOKEN GITHUB_TOKEN || true

echo "=== OWNER · AUTHORIZE IN YOUR OWN BROWSER ==="
echo "https://github.com/login/device"
gh auth login --web --git-protocol https
gh auth status >/dev/null

echo "=== FETCH IMMUTABLE DH ANSIBLE BOOTSTRAP ==="
gh api \
  "repos/DigitalHouses/dh_ansible/contents/bootstrap/semaphore-host-install.py?ref=$CODE_RELEASE" \
  > "$WORK/release.json"

python3 - "$WORK/release.json" "$WORK/code-install.py" "$CODE_INSTALLER_BLOB" <<'PY'
import base64, hashlib, json, os, pathlib, sys
source, target, expected_sha = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2]), sys.argv[3]
record = json.loads(source.read_text())
if record.get("type") != "file" or record.get("encoding") != "base64":
    raise SystemExit("BLOCKED: pinned dh_ansible bootstrap file missing")
blob = base64.b64decode(record["content"])
identity = hashlib.sha1(b"blob " + str(len(blob)).encode() + b"\0" + blob).hexdigest()
if identity != expected_sha or record.get("sha") != expected_sha:
    raise SystemExit("BLOCKED: dh_ansible bootstrap immutable Git identity mismatch")
fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o700)
with os.fdopen(fd, "wb") as output:
    output.write(blob)
print("DH_ANSIBLE_INSTALLER_SOURCE=VERIFIED")
PY

echo "=== READ NETWORK CONFIGURATION ==="
BRIDGE_IP=$(ip -4 -o addr show dev vmbr0 | awk '$3=="inet" {print $4}')
DEFAULT_GW=$(ip -4 route show default | awk '$1=="default" && $2=="via" {print $3}')
readarray -t NETWORK < <(python3 - "$BRIDGE_IP" "$DEFAULT_GW" <<'PY'
import ipaddress,sys
try:
    interface = ipaddress.IPv4Interface(sys.argv[1])
    gateway = ipaddress.IPv4Address(sys.argv[2])
    assert interface.network.prefixlen == 24
    assert gateway in interface.network
    address = ipaddress.IPv4Address(int(interface.network.network_address) + 231)
    assert address != interface.ip and address != gateway
except (IndexError, ValueError, AssertionError):
    raise SystemExit("BLOCKED: unsupported or ambiguous PVE LAN")
print(interface.ip)
print(str(address) + "/24")
print(gateway)
PY
)
if (( ${#NETWORK[@]} != 3 )); then
  echo "BLOCKED: PVE network resolution failed" >&2; exit 2
fi
echo "PVE=${NETWORK[0]} RUNNER=${NETWORK[1]}"
echo "=== RUN PINNED DH ANSIBLE INSTALLER ==="
python3 "$WORK/code-install.py" \
  --site "$SITE" \
  --pve-ip "${NETWORK[0]}" \
  --runner-cidr "${NETWORK[1]}" \
  --gateway "${NETWORK[2]}" \
  --expected-commit "$CODE_RELEASE" \
  --expected-fleet-commit "$FLEET_RELEASE" \
  --use-github-session --apply "${RESUME_ARGS[@]}"

echo "=== DONE ==="
