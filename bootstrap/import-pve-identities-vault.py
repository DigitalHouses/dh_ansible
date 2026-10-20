#!/usr/bin/env python3
"""One-time PVE root / site-runner import of PVE bootstrap secrets into Fleet Vault.

Host mode runs on PVE as root. Worker mode runs as root inside the selected runner.
Only encrypted Fleet Vault ciphertext is published for Git commit.
"""
from __future__ import annotations

import argparse
import copy
import getpass
import hashlib
import ipaddress
import json
import os
import pwd
import re
import stat
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

STAGING_BASE = Path("/var/lib/digitalhouses/bootstrap")
PRIVATE_KEY = Path("/var/lib/ansible/.ssh/pve_host_write_ed25519")
PUBLIC_KEY = Path("/var/lib/ansible/.ssh/pve_host_write_ed25519.pub")
VAULT_BASE_DIR = Path("/dev/shm")


def vault_base(site: str) -> Path:
    return VAULT_BASE_DIR / f"fleet.{site}.base.vault.yml"


def vault_candidate(site: str) -> Path:
    return VAULT_BASE_DIR / f"fleet.{site}.candidate.vault.yml"


def blocked(message: str):
    raise RuntimeError(f"BLOCKED: {message}")


def checked(*argv: str) -> str:
    result = subprocess.run(argv, capture_output=True, text=True, check=False)
    if result.returncode:
        # Do not print captured stderr/stdout; it may contain credentials.
        blocked(f"Command failed (rc={result.returncode}, executable={Path(argv[0]).name})")
    return result.stdout.strip()


def pct(ctid: int, *argv: str) -> str:
    return checked("pct", "exec", str(ctid), "--", *argv)


def secure_read(path: Path, require_uid: int = 0) -> bytes:
    if path.is_symlink() or not path.is_file():
        blocked(f"Missing or linked file: {path.name}")
    s = path.stat()
    if s.st_uid != require_uid or stat.S_IMODE(s.st_mode) != 0o600:
        blocked(f"Unsafe owner/mode: {path.name}")
    return path.read_bytes()


def recover_worker(args):
    """Restore only canonical site SSH key from approved encrypted Fleet Vault.

    Returns ABSENT when the site has no canonical credentials, never prints
    tokens or SSH key. Partial credential namespaces fail closed.
    """
    from ansible.parsing.vault import VaultLib, VaultSecret
    import yaml
    if os.geteuid() != 0 or checked("hostname") != f"ansible-{args.site}":
        blocked("Wrong runner for canonical PVE recovery")
    master = secure_read(Path(args.master_file)).rstrip(b"\r\n")
    plaintext = VaultLib([("default", VaultSecret(master))]).decrypt(
        secure_read(Path(args.vault_file)))
    vault = yaml.safe_load(plaintext)
    if isinstance(vault, dict) and isinstance(vault.get("site_passport"), dict):
        passport = vault["site_passport"]
        if passport.get("site_id") != args.site:
            blocked("Wrong site passport")
        site = passport.get("site_secrets", {})
    else:
        site = vault.get("fleet_secrets", {}).get("sites", {}).get(args.site, {})
    if not isinstance(site, dict) or not isinstance(site.get("pve", {}), dict):
        blocked("Unexpected canonical site secrets schema")
    pve = site.get("pve", {})
    keys = ("audit_token_secret", "write_token_secret", "host_write_ssh_private_key")
    present = [isinstance(pve.get(k), str) and bool(pve.get(k)) for k in keys]
    if not any(present):
        print("PVE_FLEET_RECOVERY=ABSENT")
        return
    if not all(present):
        blocked("Incomplete canonical site PVE secret namespace")
    if any(not re.fullmatch(r"[0-9a-fA-F-]{36}", pve[k]) for k in keys[:2]):
        blocked("Malformed canonical API token secret")
    key = pve[keys[2]]
    if "BEGIN OPENSSH PRIVATE KEY" not in key or len(key) > 16384:
        blocked("Malformed canonical SSH private key")
    private = PRIVATE_KEY
    if private.is_symlink() or private.with_suffix(private.suffix + ".pub").is_symlink():
        blocked("SSH identity is a symlink")
    private.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(private.parent, 0o700)
    key_bytes = (key.rstrip("\r\n") + "\n").encode()
    if private.exists():
        if secure_read(private) != key_bytes:
            blocked("Existing runner SSH key conflicts with encrypted Fleet Vault")
    else:
        fd = os.open(private, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "wb") as out:
            out.write(key_bytes)
            out.flush()
            os.fsync(out.fileno())
    public_line = checked("ssh-keygen", "-y", "-P", "", "-f", str(private)).strip()
    if len(public_line.split()) < 2 or public_line.split()[0] != "ssh-ed25519":
        blocked("Recovered SSH key is not ED25519")
    if PUBLIC_KEY.exists():
        if PUBLIC_KEY.read_text().strip().split()[:2] != public_line.split()[:2]:
            blocked("Existing runner SSH public key conflicts with Fleet Vault")
    else:
        fd = os.open(PUBLIC_KEY, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        with os.fdopen(fd, "w") as out:
            out.write(public_line + "\n")
    print("PVE_FLEET_RECOVERY=PASS")


def worker(args):
    from ansible.parsing.vault import VaultLib, VaultSecret
    import yaml

    if os.geteuid() != 0:
        blocked("Guest worker must run as root inside runner")
    if args.ctid != 1231:
        blocked("Invalid runner CTID")
    if checked("hostname") != f"ansible-{args.site}":
        blocked("Runner hostname mismatch")
    home_uid = 0
    master = secure_read(Path(args.master_file)).rstrip(b"\r\n")
    if len(master) < 8:
        blocked("Invalid master-password length")
    stage = json.loads(secure_read(Path(args.staged_tokens)).decode("utf-8"))
    if stage.get("site_id") != args.site or stage.get("identity_schema") != 2:
        blocked("PVE bootstrap staging schema mismatch")
    if stage.get("principals") != {"audit": "ansible-audit@pve", "write": "ansible-writer@pve", "linux": "ansible-linux"}:
        blocked("PVE bootstrap staging principals mismatch")
    if stage.get("runner_ip") != args.runner_ip or stage.get("pve_ip") != args.pve_ip:
        blocked("PVE bootstrap staging IP mismatch")
    tokens = stage.get("pve_tokens")
    if not isinstance(tokens, dict) or set(tokens) != {"audit", "write"}:
        blocked("PVE bootstrap must contain both tokens, and no other tokens")
    if any(not isinstance(v, str) or not re.fullmatch(r"[0-9a-fA-F-]{36}", v)
           for v in tokens.values()):
        blocked("Malformed PVE token secret")
    ssh_key = secure_read(PRIVATE_KEY, require_uid=home_uid).decode("utf-8")
    public = PUBLIC_KEY.read_text("utf-8").strip().split()
    if len(public) < 2 or public[0] != "ssh-ed25519":
        blocked("Runner public host-write key missing")
    generated = checked("ssh-keygen", "-y", "-P", "", "-f", str(PRIVATE_KEY)).split()
    if generated[:2] != public[:2]:
        blocked("SSH private/public key mismatch")
    candidate = Path(args.candidate_file)
    if candidate.exists() or candidate.is_symlink():
        blocked("Existing candidate; do not overwrite")
    base = secure_read(Path(args.vault_file), require_uid=home_uid)
    if not base.startswith(b"$ANSIBLE_VAULT;"):
        blocked("Git source is not encrypted Vault data")
    secret = VaultSecret(master)
    vault = VaultLib([("default", secret)])
    try:
        plain = vault.decrypt(base)
    except Exception:
        blocked("Fleet master password invalid or Vault unreadable")
    try:
        original = yaml.safe_load(plain)
    except Exception:
        blocked("Fleet Vault YAML is not compatible with safe loader")
    if not isinstance(original, dict) or not isinstance(original.get("fleet_secrets"), dict):
        blocked("Fleet Vault top-level fleet_secrets mapping missing")
    fs = original["fleet_secrets"]
    if not isinstance(fs.get("sites"), dict):
        blocked("Fleet Vault sites mapping missing")
    previous_sites = copy.deepcopy(fs["sites"])
    prior = fs["sites"].get(args.site, {})
    if not isinstance(prior, dict):
        blocked("Existing site secret namespace is not a mapping")
    existing_pve = prior.get("pve", {})
    if not isinstance(existing_pve, dict):
        blocked("Existing site pve secret namespace is not a mapping")
    additions = {
        "audit_token_secret": tokens["audit"],
        "write_token_secret": tokens["write"],
        "host_write_ssh_private_key": ssh_key,
    }
    for key, value in additions.items():
        if key in existing_pve and existing_pve[key] != value:
            blocked(f"Conflicting existing site secret '{key}'; no overwrite")
    target = copy.deepcopy(original)
    new_site = target["fleet_secrets"]["sites"].setdefault(args.site, {})
    target_pve = new_site.setdefault("pve", {})
    target_pve.update(additions)
    if {k: v for k, v in target["fleet_secrets"]["sites"].items() if k != args.site} != {
        k: v for k, v in previous_sites.items() if k != args.site
    }:
        blocked("Other site namespaces were modified")
    serialized = yaml.safe_dump(target, allow_unicode=True, sort_keys=False).encode("utf-8")
    encrypted = vault.encrypt(serialized, secret=secret)
    if vault.decrypt(encrypted) != serialized:
        blocked("Encrypted candidate verification failed")
    if yaml.safe_load(vault.decrypt(encrypted)) != target:
        blocked("Candidate content differs after roundtrip")
    fd = os.open(str(candidate),
                 os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(encrypted)
        f.flush()
        os.fsync(f.fileno())
    os.chown(candidate, 0, 0)
    os.chmod(candidate, 0o600)
    print("VAULT_CANDIDATE=PASS")
    print(f"VAULT_CANDIDATE_SHA256={hashlib.sha256(encrypted).hexdigest()}")
    print(f"SITE={args.site}")
    print("SECRETS_IN_OUTPUT=NO")
    print("GIT_COMMIT=PENDING")


def host(args):
    if os.geteuid() != 0:
        blocked("Run from PVE as root")
    if args.ctid != 1231:
        blocked("Fleet runner CTID must be 1231")
    if not re.fullmatch(r"[a-z][a-z0-9_-]{0,30}", args.site):
        blocked("Invalid site identifier")
    if ipaddress.ip_address(args.runner_ip).packed[-1] != 231:
        blocked("Fleet runner IPv4 must end in .231")
    for addr in (args.runner_ip, args.pve_ip):
        if ipaddress.ip_address(addr).version != 4:
            blocked("Only IPv4 is supported")
    if not Path("/etc/pve/user.cfg").exists():
        blocked("This is not a PVE node")
    if checked("pct", "status", str(args.ctid)) != "status: running":
        blocked("Runner LXC is not running")
    if pct(args.ctid, "hostname") != f"ansible-{args.site}":
        blocked("Runner hostname mismatch")
    staging = STAGING_BASE / f"pve-identities-{args.site}-ansible-v2-pending.json"
    bundle = json.loads(secure_read(staging).decode("utf-8"))
    if bundle.get("site_id") != args.site or bundle.get("identity_schema") != 2:
        blocked("PVE identity schema mismatch")
    if bundle.get("principals") != {"audit": "ansible-audit@pve", "write": "ansible-writer@pve", "linux": "ansible-linux"}:
        blocked("PVE identity principal mismatch")
    if bundle.get("runner_ip") != args.runner_ip or bundle.get("pve_ip") != args.pve_ip:
        blocked("PVE identity staging address mismatch")
    if set(bundle.get("pve_tokens", {})) != {"audit", "write"}:
        blocked("Both PVE tokens must exist before Vault import")
    if not args.vault_file or not args.master_file or not args.output_file:
        blocked("PVE must provide trusted vault, master, and encrypted output paths")
    secure_read(Path(args.vault_file))
    secure_read(Path(args.master_file))
    print("PVE_STAGE=PASS")
    print("RUNNER_VAULT_SOURCE=PASS")
    print("MASTER_PASSWORD=LOCAL_TERMINAL_ONLY")
    master = secure_read(Path(args.master_file))
    if not 8 <= len(master.rstrip(b"\r\n")) <= 1024:
        blocked("Master password invalid")
    guest_temp = f"/dev/shm/dh-fleet-{uuid.uuid4().hex}"
    with tempfile.TemporaryDirectory(prefix="dh-fleet-", dir="/run") as host_tmp:
        host_master = Path(host_tmp) / "master"
        host_master.write_bytes(master)
        os.chmod(host_master, 0o600)
        del master
        try:
            pct(args.ctid, "install", "-d", "-m", "0700", guest_temp)
            checked("pct", "push", str(args.ctid), str(host_master), guest_temp + "/master")
            checked("pct", "push", str(args.ctid), str(staging), guest_temp + "/staging.json")
            checked("pct", "push", str(args.ctid), str(args.vault_file), guest_temp + "/base.vault.yml")
            pct(args.ctid, "chmod", "0600", guest_temp + "/master",
                guest_temp + "/staging.json", guest_temp + "/base.vault.yml")
            print(pct(args.ctid, "/opt/ansible/venv/bin/python",
                      "/opt/digitalhouses/bootstrap/import-pve-identities-vault.py",
                      "--worker", "--site", args.site, "--ctid", str(args.ctid),
                      "--runner-ip", args.runner_ip, "--pve-ip", args.pve_ip,
                      "--master-file", guest_temp + "/master",
                      "--staged-tokens", guest_temp + "/staging.json",
                      "--vault-file", guest_temp + "/base.vault.yml",
                      "--candidate-file", guest_temp + "/candidate.vault.yml"))
            if Path(args.output_file).exists():
                blocked("Encrypted candidate output already exists")
            checked("pct", "pull", str(args.ctid), guest_temp + "/candidate.vault.yml",
                    str(args.output_file))
            os.chmod(args.output_file, 0o600)
            encrypted = secure_read(Path(args.output_file))
            if not encrypted.startswith(b"$ANSIBLE_VAULT;"):
                blocked("Returned candidate is not Ansible Vault ciphertext")
            print("ENCRYPTED_OUTPUT=PASS")
        finally:
            subprocess.run(["pct", "exec", str(args.ctid), "--",
                            "rm", "-f", guest_temp + "/master", guest_temp + "/staging.json",
                            guest_temp + "/base.vault.yml", guest_temp + "/candidate.vault.yml"],
                           capture_output=True, check=False)
            subprocess.run(["pct", "exec", str(args.ctid), "--",
                            "rmdir", guest_temp], capture_output=True, check=False)
    print("TEMPORARY_MASTER_FILE=CLEANUP_ATTEMPTED")
    print("PVE_STAGING=RETAINED_UNTIL_GIT_VERIFY")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--recover", action="store_true")
    parser.add_argument("--site", required=True)
    parser.add_argument("--ctid", type=int, required=True)
    parser.add_argument("--runner-ip", required=True)
    parser.add_argument("--pve-ip", required=True)
    parser.add_argument("--master-file")
    parser.add_argument("--staged-tokens")
    parser.add_argument("--vault-file")
    parser.add_argument("--candidate-file")
    parser.add_argument("--output-file")
    args = parser.parse_args()
    if args.recover:
        if not args.master_file or not args.vault_file:
            blocked("Runner recovery requires secure temporary Fleet Vault files")
        recover_worker(args)
    elif args.worker:
        if not args.master_file or not args.staged_tokens or not args.vault_file or not args.candidate_file:
            blocked("Guest worker requires secret file paths")
        worker(args)
    else:
        host(args)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # Deliberately never print exception cause containing credential values.
        print(f"VAULT_IMPORT=BLOCKED: {exc}", file=sys.stderr)
        sys.exit(1)
