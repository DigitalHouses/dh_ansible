#!/usr/bin/env python3
"""DigitalHouses PVE bootstrap: two API identities and one restricted SSH identity.

Run once as root on the PVE node, after the site runner is created.
Token secrets are saved in a root-only temporary bundle until Fleet Vault import.
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import os
import pwd
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

AUDIT_USER = "ansible-audit@pve"
WRITE_USER = "ansible-writer@pve"
HOST_USER = "ansible-linux"
KEY_PATH = "/var/lib/ansible/.ssh/pve_host_write_ed25519"
STAGING_DIR = Path("/var/lib/digitalhouses/bootstrap")
HOST_HOME = "/var/lib/ansible-linux"


def fail(message: str) -> None:
    raise RuntimeError(message)


def run(*argv: str) -> str:
    p = subprocess.run(argv, text=True, capture_output=True, check=False)
    if p.returncode:
        fail(f"Command failed (rc={p.returncode}): {argv[0]} {argv[1] if len(argv)>1 else ''}; "
             f"stderr={p.stderr.strip()[:300]}")
    return p.stdout.strip()


def read_json(*args: str):
    return json.loads(run(*args, "--output-format", "json"))


def records(obj, *, name: str) -> list[dict]:
    if not isinstance(obj, list) or any(not isinstance(x, dict) for x in obj):
        fail(f"Unexpected PVE JSON response for {name}")
    return obj


def exact_acl(rows: list[dict], kind: str, identity: str, role: str) -> bool:
    current = [r for r in rows if r.get("type") == kind and r.get("ugid") == identity]
    expected = {"type": kind, "ugid": identity, "path": "/", "roleid": role}
    for item in current:
        if any(item.get(k) != v for k, v in expected.items()):
            fail(f"BLOCKED: Unexpected pre-existing ACL on {identity}: {item.get('path')}/{item.get('roleid')}")
        if str(item.get("propagate", "1")).lower() not in ("1", "true"):
            fail(f"BLOCKED: ACL propagation disabled for {identity}")
    return bool(current)


def save_staging(path: Path, obj: dict) -> None:
    fd, tmp = tempfile.mkstemp(prefix=".identities-", dir=str(path.parent))
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, sort_keys=True, indent=2)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def runner_exec(ctid: str, *cmd: str) -> str:
    return run("pct", "exec", ctid, "--", *cmd)


def prepare_runner_key(ctid: str, site: str) -> str:
    runner_exec(ctid, "install", "-d", "-m", "0700", "/var/lib/ansible/.ssh")
    pub_path = KEY_PATH + ".pub"
    state = runner_exec(
        ctid, "python3", "-c",
        "import pathlib,sys; a=pathlib.Path(sys.argv[1]);b=pathlib.Path(sys.argv[2]);"
        "print(int(a.exists()),int(b.exists()))", KEY_PATH, pub_path,
    )
    if state == "0 0":
        runner_exec(ctid, "ssh-keygen", "-q", "-t", "ed25519", "-N", "",
                         "-f", KEY_PATH, "-C", f"digitalhouses-{site}-pve-host")
    elif state != "1 1":
        fail("BLOCKED: Incomplete runner SSH key pair; no overwrite")
    key = runner_exec(ctid, "cat", pub_path).strip()
    if not re.fullmatch(r"ssh-ed25519 [A-Za-z0-9+/=]+(?: [^\r\n]+)?", key):
        fail("Invalid runner ED25519 public key")
    # Semaphore cannot read the PVE host-write private key.
    mode = runner_exec(ctid, "stat", "-c", "%a:%u:%g", KEY_PATH)
    if mode != "600:0:0":
        fail("BLOCKED: Runner host-write private key must remain root-only")
    return key


def install_known_host(ctid: str, pve_ip: str) -> None:
    hostpub = Path("/etc/ssh/ssh_host_ed25519_key.pub").read_text().strip().split()
    if len(hostpub) < 2 or hostpub[0] != "ssh-ed25519":
        fail("Missing PVE SSH ED25519 host key")
    line = f"{pve_ip} {hostpub[0]} {hostpub[1]}"
    script = '''import os,pathlib,sys
p=pathlib.Path('/var/lib/ansible/.ssh/pve_host_audit_known_hosts')
line=sys.argv[1]
if p.is_symlink(): raise SystemExit('BLOCKED: known_hosts symlink')
if p.exists():
    rows=p.read_text().splitlines()
    if rows!=[line]: raise SystemExit('BLOCKED: existing known_hosts differs; no overwrite')
else:
    fd=os.open(str(p),os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    with os.fdopen(fd,'w') as f: f.write(line+'\\n')
print('PINNED_HOST_KEY=PASS')
'''
    print(runner_exec(ctid, "python3", "-c", script, line))


def install_linux_user(source_ip: str, key: str) -> None:
    try:
        account = pwd.getpwnam(HOST_USER)
    except KeyError:
        run("useradd", "--system", "--user-group", "--create-home",
            "--home-dir", HOST_HOME, "--shell", "/bin/bash", HOST_USER)
        account = pwd.getpwnam(HOST_USER)
    if account.pw_uid == 0 or account.pw_dir != HOST_HOME or account.pw_shell != "/bin/bash":
        fail("BLOCKED: Existing Linux ansible-linux identity differs")
    shadow = run("getent", "shadow", HOST_USER).split(":", 2)[1]
    if not shadow.startswith(("!", "*")):
        fail("BLOCKED: Existing Linux ansible-linux has an enabled password")
    sshdir = Path(account.pw_dir) / ".ssh"
    if sshdir.is_symlink():
        fail("BLOCKED: Linux .ssh is symlink")
    sshdir.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chown(sshdir, account.pw_uid, account.pw_gid)
    os.chmod(sshdir, 0o700)
    authorized = sshdir / "authorized_keys"
    if authorized.is_symlink():
        fail("BLOCKED: Linux authorized_keys is symlink")
    existing = authorized.read_text().splitlines() if authorized.exists() else []
    parts = key.split()
    for line in existing:
        if parts[1] in line and not line.startswith(f'from="{source_ip}",restrict '):
            fail("BLOCKED: Runner public key exists with different SSH restrictions")
    managed_line = f'from="{source_ip}",restrict {parts[0]} {parts[1]} digitalhouses-pve-host-write'
    if managed_line not in existing:
        if authorized.exists() and authorized.stat().st_size and not authorized.read_bytes().endswith(b"\n"):
            fail("BLOCKED: authorized_keys lacks newline; no append")
        with authorized.open("a") as fh:
            fh.write(managed_line + "\n")
    os.chown(authorized, account.pw_uid, account.pw_gid)
    os.chmod(authorized, 0o600)
    sudoers = Path("/etc/sudoers.d/90-digitalhouses-ansible-linux")
    desired = "ansible-linux ALL=(root) NOPASSWD: ALL\n"
    if sudoers.is_symlink():
        fail("BLOCKED: sudoers path symlink")
    if sudoers.exists() and sudoers.read_text() != desired:
        fail("BLOCKED: Existing sudoers policy conflicts; no overwrite")
    if not sudoers.exists():
        fd = os.open(sudoers, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o440)
        with os.fdopen(fd, "w") as fh:
            fh.write(desired)
    run("visudo", "-cf", str(sudoers))
    os.chmod(sudoers, 0o440)
    print("LINUX_SSH_SUDO=PASS")


def ensure_pve_user(user: str, role: str, users: list[dict], acls: list[dict]) -> None:
    existing = next((u for u in users if u.get("userid") == user), None)
    if existing is not None:
        if str(existing.get("enable", "1")).lower() not in ("1", "true"):
            fail(f"BLOCKED: Existing {user} disabled")
        if existing.get("groups") not in (None, ""):
            fail(f"BLOCKED: Existing {user} has groups")
    else:
        run("pveum", "user", "add", user, "--comment", "DigitalHouses Ansible site runner")
    if not exact_acl(acls, "user", user, role):
        run("pveum", "acl", "modify", "/", "--users", user, "--roles", role, "--propagate", "1")


def ensure_api_token(user: str, tokenid: str, role: str, staging: dict, path: Path) -> None:
    fullid = f"{user}!{tokenid}"
    tokens = records(read_json("pveum", "user", "token", "list", user),
                     name=f"tokens for {user}")
    old = next((t for t in tokens if t.get("tokenid") == tokenid), None)
    acls = records(read_json("pveum", "acl", "list"), name="ACL list")
    exact_acl(acls, "token", fullid, role)
    existing_value = staging["pve_tokens"].get(tokenid)
    if old is not None:
        if str(old.get("privsep", "1")).lower() not in ("1", "true"):
            fail(f"BLOCKED: Privilege separation disabled for {fullid}")
        if not isinstance(existing_value, str) or not existing_value:
            fail(f"BLOCKED: Existing {fullid} token secret unavailable. Do not regenerate automatically.")
    else:
        if existing_value is not None:
            fail(f"BLOCKED: Staged {fullid} secret exists but PVE token is missing")
        response = read_json("pveum", "user", "token", "add", user, tokenid,
                             "--privsep", "1", "--comment", "DigitalHouses Ansible bootstrap")
        secret = response.get("value") if isinstance(response, dict) else None
        if not isinstance(secret, str) or not re.fullmatch(r"[0-9a-fA-F-]{36}", secret):
            fail(f"BLOCKED: Token creation response for {fullid} unexpected; do not retry blindly")
        staging["pve_tokens"][tokenid] = secret
        save_staging(path, staging)
    if not exact_acl(acls, "token", fullid, role):
        run("pveum", "acl", "modify", "/", "--tokens", fullid, "--roles", role, "--propagate", "1")
    print(f"{fullid}=PASS")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site", required=True)
    parser.add_argument("--runner-ctid", required=True, type=int)
    parser.add_argument("--runner-ip", required=True)
    parser.add_argument("--pve-ip", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--reconcile-restored", action="store_true")
    args = parser.parse_args()
    if os.geteuid() != 0:
        fail("Run as root on PVE")
    if not re.fullmatch("[a-z][a-z0-9_-]{0,30}", args.site):
        fail("Invalid site identifier")
    source = ipaddress.ip_address(args.runner_ip)
    target = ipaddress.ip_address(args.pve_ip)
    if source.version != 4 or target.version != 4:
        fail("IPv4 addresses required")
    if args.runner_ctid != 1231 or source.packed[-1] != 231:
        fail("BLOCKED: Fleet runner identity must use CT1231 and IPv4 .231")
    for name in ("pveum", "pct", "ssh-keygen", "visudo"):
        if not shutil.which(name):
            fail(f"BLOCKED: Required PVE utility {name} not available")
    if not Path("/etc/pve/user.cfg").exists():
        fail("BLOCKED: Not a Proxmox VE control host")
    local_addresses = run("ip", "-4", "-o", "addr", "show")
    if not any(str(target) == row.split()[3].split("/")[0]
               for row in local_addresses.splitlines() if len(row.split()) >= 4):
        fail("BLOCKED: PVE IP not assigned to this host")
    conf = run("pct", "config", str(args.runner_ctid))
    if f"hostname: ansible-{args.site}" not in conf or f"ip={source}/" not in conf:
        fail("BLOCKED: Runner CTID/site/IP does not match PVE configuration")
    if run("pct", "status", str(args.runner_ctid)) != "status: running":
        fail("BLOCKED: Runner is not running")
    if runner_exec(str(args.runner_ctid), "hostname") != f"ansible-{args.site}":
        fail("BLOCKED: Runner guest hostname mismatch")
    if source == target:
        fail("BLOCKED: Runner and PVE addresses must differ")
    users = records(read_json("pveum", "user", "list"), name="users")
    acl = records(read_json("pveum", "acl", "list"), name="ACL list")
    for u, role in ((AUDIT_USER, "PVEAuditor"), (WRITE_USER, "PVEAdmin")):
        exact_acl(acl, "user", u, role)
    for u, token, role in ((AUDIT_USER, "audit", "PVEAuditor"), (WRITE_USER, "write", "PVEAdmin")):
        exact_acl(acl, "token", f"{u}!{token}", role)
    # The verified encrypted site Vault is the source of restored credentials.
    # Here PVE root verifies identities and reauthorizes exactly the matching
    # restored runner SSH public key, without issuing/rotating API tokens.
    if args.reconcile_restored:
        for user, token, role in ((AUDIT_USER, "audit", "PVEAuditor"),
                                  (WRITE_USER, "write", "PVEAdmin")):
            if not any(row.get("userid") == user for row in users):
                fail(f"BLOCKED: Restored API principal missing: {user}")
            ensure_list = records(
                read_json("pveum", "user", "token", "list", user),
                name=f"restored token for {user}")
            if not any(row.get("tokenid") == token
                       and str(row.get("privsep", "1")).lower() in ("1", "true")
                       for row in ensure_list):
                fail(f"BLOCKED: Restored PVE token missing or privileges differ: {user}")
            if not exact_acl(acl, "user", user, role):
                fail(f"BLOCKED: Restored API user ACL missing: {user}")
            if not exact_acl(acl, "token", f"{user}!{token}", role):
                fail(f"BLOCKED: Restored API token ACL missing: {user}")
        key_file_state = runner_exec(str(args.runner_ctid), "stat", "-c",
                                     "%a:%u:%g", KEY_PATH)
        if key_file_state != "600:0:0":
            fail("BLOCKED: Restored runner private key missing or unsafe")
        restored_pub = runner_exec(str(args.runner_ctid), "cat", KEY_PATH + ".pub")
        if not re.fullmatch(r"ssh-ed25519 [A-Za-z0-9+/=]+(?: [^\r\n]+)?",
                            restored_pub):
            fail("BLOCKED: Restored runner public key invalid")
        if not args.apply:
            print("PVE_RESTORED_IDENTITIES=READ_ONLY_PASS")
            return
        install_linux_user(str(source), restored_pub)
        install_known_host(str(args.runner_ctid), str(target))
        print("PVE_RESTORED_IDENTITIES=PASS (tokens preserved)")
        return

    # Never issue a second credential if an existing PVE token or host
    # identity has lost its restore material. This gate is read-only.
    path = STAGING_DIR / f"pve-identities-{args.site}-ansible-v2-pending.json"
    staged = path.is_file() and not path.is_symlink()
    if path.exists() and not staged:
        fail("BLOCKED: Unexpected canonical identity staging type")
    for user, token in ((AUDIT_USER, "audit"), (WRITE_USER, "write")):
        if any(u.get("userid") == user for u in users):
            existing = records(read_json("pveum", "user", "token", "list", user),
                               name=f"canonical token list for {user}")
            if any(row.get("tokenid") == token for row in existing) and not staged:
                fail("BLOCKED: Existing canonical PVE token has no v2 escrow; restore from Fleet Vault")
    key_state = runner_exec(
        str(args.runner_ctid), "python3", "-c",
        "import pathlib; p=pathlib.Path('/var/lib/ansible/.ssh/pve_host_write_ed25519');"
        "q=pathlib.Path(str(p)+'.pub'); print(int(p.exists()),int(q.exists()))",
    )
    if key_state == "0 0" and staged:
        fail("BLOCKED: Rebuilt runner has no PVE private key; restore from Fleet Vault, do not rotate")
    if key_state == "0 0":
        try:
            pwd.getpwnam(HOST_USER)
        except KeyError:
            pass
        else:
            fail("BLOCKED: Existing canonical PVE Linux principal but no runner key; recover first")
    print(f"PREFLIGHT=PASS SITE={args.site} RUNNER_CTID={args.runner_ctid}")
    if not args.apply:
        print("MODE=READ_ONLY; add --apply after reviewing the plan")
        return
    STAGING_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(STAGING_DIR, 0o700)
    # Identity v2 intentionally does not reuse historical token escrow.
    if path.is_symlink():
        fail("BLOCKED: Staging path symlink")
    if path.exists():
        if path.stat().st_uid != 0 or path.stat().st_mode & 0o077:
            fail("BLOCKED: Unsafe staging file permissions")
        staging = json.loads(path.read_text())
        if staging.get("site_id") != args.site or staging.get("identity_schema") != 2:
            fail("BLOCKED: Staging identity schema mismatch")
        if staging.get("principals") != {"audit": AUDIT_USER, "write": WRITE_USER, "linux": HOST_USER}:
            fail("BLOCKED: Staging principal mismatch")
            fail("BLOCKED: Staging file site mismatch")
    else:
        staging = {"site_id": args.site, "identity_schema": 2,
                   "principals": {"audit": AUDIT_USER, "write": WRITE_USER, "linux": HOST_USER},
                   "runner_ip": str(source),
                   "pve_ip": str(target), "pve_tokens": {}, "status": "VAULT_PENDING"}
        save_staging(path, staging)
    if staging.get("runner_ip") != str(source) or staging.get("pve_ip") != str(target):
        fail("BLOCKED: Staging file identity mismatch")
    key = prepare_runner_key(str(args.runner_ctid), args.site)
    install_linux_user(str(source), key)
    install_known_host(str(args.runner_ctid), str(target))
    for user, role in ((AUDIT_USER, "PVEAuditor"), (WRITE_USER, "PVEAdmin")):
        ensure_pve_user(user, role, users, acl)
    for user, token, role in ((AUDIT_USER, "audit", "PVEAuditor"), (WRITE_USER, "write", "PVEAdmin")):
        ensure_api_token(user, token, role, staging, path)
    print("BOOTSTRAP=PASS")
    print("CREDENTIALS=ROOT_ONLY_STAGING")
    print("VAULT_IMPORT=PENDING (not yet in encrypted fleet Vault)")
    print(f"PENDING_FILE={path}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"BOOTSTRAP=BLOCKED: {exc}", file=sys.stderr)
        sys.exit(1)
