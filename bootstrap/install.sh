#!/usr/bin/env bash
# Single-repository public PVE entrypoint: immutable dh_ansible code + private Fleet Detail data.
set -Eeuo pipefail
umask 077

SITE=""
CODE_RELEASE="0d5e6668813ea7c9e3c6dee7599d87941320b6ad"
CODE_INSTALLER_BLOB="bc8148461d66215a9cb83c199ebece35f6652ca4"
FLEET_RELEASE="2d0a555b920bb2bafd630678eaea1be325908d4c"
GH_VERSION="2.102.0"
GH_SHA256="bb766f710eef8ede859c18578c72c327597cd4c8a85b06001b1f3843c6019386"

RESUME_ARGS=()
if (( $# == 1 )); then
  case "$1" in
    --resume|--resume-fresh|--resume-ansible|--resume-semaphore) RESUME_ARGS=("$1") ;;
    *) echo "BLOCKED: unknown argument. Site ID is entered interactively." >&2; exit 2 ;;
  esac
elif (( $# != 0 )); then
  echo "BLOCKED: usage: install.sh [--resume|--resume-fresh|--resume-ansible|--resume-semaphore]" >&2
  exit 2
fi
if (( EUID != 0 )) || [[ ! -f /etc/pve/user.cfg ]]; then
  echo "BLOCKED: root terminal on Proxmox VE required" >&2
  exit 2
fi

confirm_site_id() {
  local candidate answer original
  echo "=== CANONICAL SITE CONFIRMATION ==="
  while true; do
    if ! IFS= read -r -p "Введите Site ID (<owner>-<type>): " candidate; then
      echo "BLOCKED: Site ID input interrupted; no actions performed" >&2
      return 2
    fi
    # Pasted terminal text can carry trailing spaces, TAB or CR (Windows).
    # Trim only edge whitespace; never silently modify the Site ID itself.
    original="$candidate"
    while [[ -n "$candidate" && "${candidate:0:1}" =~ [[:space:]] ]]; do
      candidate="${candidate:1}"
    done
    while [[ -n "$candidate" && "${candidate: -1}" =~ [[:space:]] ]]; do
      candidate="${candidate:0:${#candidate}-1}"
    done
    if [[ "$original" != "$candidate" ]]; then
      echo "SITE_INPUT_TRIMMED=YES (краевые пробельные символы удалены)"
    fi
    if [[ ! "$candidate" =~ ^[a-z][a-z0-9]*(-[a-z0-9]+)+$ ]]; then
      echo "Некорректный Site ID. Формат: <owner>-<type> (например, serzhan-home). Повторите ввод."
      continue
    fi
    echo "OBJECT_SLUG=$candidate"
    echo "DOWNSTREAM_SITE=$candidate"
    while true; do
      if ! IFS= read -r -p "Site ID '$candidate' указан верно? (y/n): " answer; then
        echo "BLOCKED: Site ID confirmation interrupted; no actions performed" >&2
        return 2
      fi
      case "$answer" in
        y|Y)
          SITE="$candidate"
          echo "SITE_CONFIRMATION=YES $SITE"
          return 0
          ;;
        n|N)
          echo "Повторный ввод Site ID."
          break
          ;;
        *)
          echo "Ответьте y или n."
          ;;
      esac
    done
  done
}
# Root-only structured diagnostic journal. Never record raw terminal output:
# GitHub device codes, Vault passwords and other arbitrary child output stay off disk.
LOG_DIR="/var/log/digitalhouses/ansible"
if [[ -L "$LOG_DIR" ]]; then
  echo "BLOCKED: installer log directory is a symlink" >&2
  exit 2
fi
install -d -o root -g root -m 0700 "$LOG_DIR"
if [[ "$(stat -c '%a:%u:%g:%F' "$LOG_DIR")" != "700:0:0:directory" ]]; then
  echo "BLOCKED: unsafe installer log directory" >&2
  exit 2
fi
LOG_FILE=$(mktemp "$LOG_DIR/install-$(date -u +%Y%m%dT%H%M%SZ).XXXXXXXX.log")
chmod 0600 "$LOG_FILE"
# Only known structured progress/error lines are persisted. Full output remains
# visible in the terminal. Never log GitHub OAuth device codes or prompts.
LOG_PATTERN='^(=== |LOG_FILE=|LOG_MODE=|RESULT_RC=|SITE_CONFIRMATION=|SITE_INPUT_TRIMMED=|OBJECT_SLUG=|DOWNSTREAM_SITE=|CODE_RELEASE=|CODE_GIT_SHA=|CODE_AND_FLEET_ARTIFACTS=|FLEET_RELEASE=|FLEET_GIT_SHA=|FLEET_MASTER_PASSWORD=PASS|FULL_VAULT_DECRYPTION=|OWNER_GITHUB_SESSION=|DH_ANSIBLE_INSTALLER_SOURCE=|PVE_ESCROW=|PVE_HOST_PREFLIGHT=|CT[0-9]+[=:]|CT_CREATE=|PCT_EXEC_FAILURE=|DEBIAN_TEMPLATE|GUEST_|FRESH_DEBIAN|NETWORK_IDENTITY_MDNS=|ANSIBLE_|SEMAPHORE_|SITE_INVENTORY_|CANONICAL_SITE_ID=|REMOTE_COMMANDER_|TLS_CERT_SHA256=|PASSWORD_ROTATION_REQUIRED=|PVE_WRITE_OPERATIONS=|PLAN: |BLOCKED: |INSTALL=BLOCKED: |DONE: )'
exec > >(tee >(LC_ALL=C grep -aE --line-buffered "$LOG_PATTERN" >> "$LOG_FILE")) 2>&1
WORK=""
installer_cleanup() {
  local rc=$?
  trap - EXIT
  if [[ -n "$WORK" ]]; then
    rm -rf "$WORK"
  fi
  echo "RESULT_RC=$rc"
  echo "LOG_FILE=$LOG_FILE"
  echo "TERMINAL STAYS OPEN"
}
trap installer_cleanup EXIT
echo "LOG_FILE=$LOG_FILE"
echo "LOG_MODE=SAFE_EVENTS_ONLY"

confirm_site_id || exit 2

WORK=$(mktemp -d /root/ansible-network.XXXXXX)
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
