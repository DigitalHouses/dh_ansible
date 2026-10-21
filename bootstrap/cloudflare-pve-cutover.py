#!/usr/bin/env python3
"""Fixed-purpose PVE Cloudflare connector cutover; no arbitrary host/command inputs at apply."""
from __future__ import annotations

import argparse
import getpass
import json
import os
from pathlib import Path
import re
import shutil
import socket
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://api.cloudflare.com/client/v4"
CONFIG = Path("/etc/digitalhouses/cloudflare/site.json")
API_TOKEN = Path("/root/.config/digitalhouses/cloudflare/api-token.secret")
CONNECTOR_TOKEN = Path("/etc/cloudflared/dh-canonical-token.secret")
DROPIN = Path("/etc/systemd/system/cloudflared.service.d/90-digitalhouses-canonical.conf")
UUID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\Z")
SITE = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)+\Z")


class Block(Exception):
    pass


def require(value, reason):
    if not value:
        raise Block(reason)


def secure_read(path):
    require(not path.is_symlink() and path.is_file(), "MISSING_OR_SYMLINK_" + path.name)
    info = path.stat()
    require(info.st_uid == 0 and stat.S_IMODE(info.st_mode) == 0o600,
            "UNSAFE_PERMISSIONS_" + path.name)
    return path.read_text(encoding="utf-8").strip()


def atomic_new(path, data, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    require(not path.is_symlink() and not path.exists(), "TARGET_ALREADY_EXISTS_" + path.name)
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0), mode)
    with os.fdopen(fd, "w", encoding="utf-8") as out:
        out.write(data)
        out.flush()
        os.fsync(out.fileno())
    os.chmod(path, mode)


def atomic_binary(path, content, mode=0o644):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    require(not path.exists() and not path.is_symlink(),
            "TARGET_ALREADY_EXISTS_" + path.name)
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY
                 | getattr(os, "O_NOFOLLOW", 0), mode)
    with os.fdopen(fd, "wb") as out:
        out.write(content)
        out.flush()
        os.fsync(out.fileno())
    os.chmod(path, mode)


def api(token, method, path):
    req = urllib.request.Request(
        BASE + path,
        headers={"Authorization": "Bearer " + token, "Accept": "application/json"},
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as e:
        raise Block(f"CF_HTTP_{e.code}_{method}") from None
    except (OSError, ValueError):
        raise Block("CF_TRANSPORT_OR_JSON") from None
    require(isinstance(payload, dict) and payload.get("success") is True,
            "CF_API_REJECTED_" + method)
    return payload.get("result")


def verify_cloudflare(token, cfg, *, require_old_healthy):
    auth = api(token, "GET", "/user/tokens/verify")
    require(isinstance(auth, dict) and auth.get("status") == "active", "API_TOKEN_INACTIVE")

    prefix = "/accounts/" + cfg["account_id"]
    old = (api(token, "GET", prefix + "/cfd_tunnel/" + cfg["old_tunnel_id"])
           if cfg.get("old_tunnel_id") else None)
    new = api(token, "GET", prefix + "/cfd_tunnel/" + cfg["new_tunnel_id"])
    if old is not None:
        require(isinstance(old, dict) and old.get("name") == cfg["old_tunnel_name"]
                and old.get("config_src") == "cloudflare", "LEGACY_TUNNEL_DRIFT")
    require(isinstance(new, dict) and new.get("name") == cfg["site_id"]
            and new.get("config_src") == "cloudflare", "CANONICAL_TUNNEL_DRIFT")
    if require_old_healthy and old is not None:
        require(old.get("status") == "healthy", "LEGACY_TUNNEL_NOT_HEALTHY")

    zone = api(token, "GET", "/zones/" + cfg["zone_id"])
    require(isinstance(zone, dict) and zone.get("name") == cfg["zone"]
            and (zone.get("account") or {}).get("id") == cfg["account_id"],
            "ZONE_ACCOUNT_DRIFT")
    ingress = api(token, "GET", prefix + "/cfd_tunnel/" + cfg["new_tunnel_id"] + "/configurations")
    rules = (ingress or {}).get("config", {}).get("ingress")
    require(isinstance(rules, list) and len(rules) == 2
            and rules[0].get("hostname") == cfg["hostname"]
            and rules[0].get("service") == cfg["origin"]
            and rules[1].get("service") == "http_status:404"
            and not rules[1].get("hostname"), "CANONICAL_INGRESS_DRIFT")
    query = urllib.parse.urlencode({"name": cfg["hostname"]})
    records = api(token, "GET", "/zones/" + cfg["zone_id"] + "/dns_records?" + query)
    require(isinstance(records, list) and len(records) == 1
            and records[0].get("type") == "CNAME"
            and records[0].get("content") == cfg["new_tunnel_id"] + ".cfargotunnel.com"
            and records[0].get("proxied") is True, "CANONICAL_DNS_DRIFT")
    app = api(token, "GET", prefix + "/access/apps/" + cfg["access_app_id"])
    policy = api(token, "GET", prefix + "/access/policies/" + cfg["access_policy_id"])
    require(isinstance(app, dict) and app.get("type") == "self_hosted"
            and app.get("domain") == "*." + cfg["zone"], "ACCESS_APP_DRIFT")
    require(isinstance(policy, dict) and policy.get("decision") == "allow"
            and policy.get("name") == "digitalhouses admin", "ACCESS_POLICY_DRIFT")
    members = [x if isinstance(x, str) else x.get("id") for x in (app.get("policies") or [])
               if isinstance(x, (str, dict))]
    require(cfg["access_policy_id"] in members, "ACCESS_POLICY_NOT_ATTACHED")
    require(len(policy.get("include") or []) == 2
            and all(isinstance(x, dict) and set(x) == {"email"} for x in policy["include"]),
            "ACCESS_ALLOWLIST_DRIFT")
    return new.get("status")


def verify_identity():
    require(os.geteuid() == 0 and Path("/etc/pve/user.cfg").is_file(),
            "PVE_ROOT_REQUIRED")


def config_from_args(args):
    require(SITE.fullmatch(args.site_id), "INVALID_SITE_ID")
    for key in ("new_tunnel_id", "account_id", "zone_id",
                "access_app_id", "access_policy_id"):
        value = getattr(args, key)
        require(bool(UUID.fullmatch(value)) if "tunnel_id" in key or "app_id" in key
                or "policy_id" in key else bool(re.fullmatch("[0-9a-f]{32}", value)),
                "INVALID_" + key)
    if args.old_tunnel_id:
        require(bool(UUID.fullmatch(args.old_tunnel_id)) and args.old_tunnel_name,
                "LEGACY_ID_OR_NAME_INVALID")
        require(args.old_tunnel_id != args.new_tunnel_id, "TUNNEL_IDS_COLLIDE")
    else:
        require(not args.old_tunnel_name, "OLD_NAME_WITHOUT_TUNNEL")
    require(args.zone == "digitalhouses.vip", "UNSUPPORTED_ZONE")
    host = args.pve_host
    require(re.fullmatch(r"192\.168\.\d{1,3}\.\d{1,3}", host), "UNEXPECTED_PVE_IP")
    return {
        "site_id": args.site_id, "account_id": args.account_id, "zone_id": args.zone_id,
        "zone": args.zone, "old_tunnel_id": args.old_tunnel_id,
        "old_tunnel_name": args.old_tunnel_name, "new_tunnel_id": args.new_tunnel_id,
        "access_app_id": args.access_app_id, "access_policy_id": args.access_policy_id,
        "pve_host": host,
        "hostname": "dh-" + args.site_id + "-pve." + args.zone,
        "origin": "https://" + host + ":8006",
    }


def enroll(args):
    cfg = config_from_args(args)
    require(not API_TOKEN.exists() or not API_TOKEN.is_symlink(), "API_TOKEN_FILE_CONFLICT")
    if CONFIG.exists() or CONFIG.is_symlink():
        require(json.loads(secure_read(CONFIG)) == cfg, "SITE_CONFIG_DRIFT")
    token = getpass.getpass("Cloudflare API token (hidden): ").strip()
    require(20 <= len(token) <= 512, "API_TOKEN_INVALID")
    verify_cloudflare(token, cfg, require_old_healthy=bool(cfg["old_tunnel_id"]))
    new_token = api(token, "GET", "/accounts/" + cfg["account_id"]
                    + "/cfd_tunnel/" + cfg["new_tunnel_id"] + "/token")
    require(isinstance(new_token, str) and 80 <= len(new_token) <= 4096,
            "CONNECTOR_TOKEN_API_NOT_AUTHORIZED")
    if CONFIG.exists():
        print("CF_SITE_CONFIG=UNCHANGED")
    else:
        atomic_new(CONFIG, json.dumps(cfg, sort_keys=True) + "\n")
        print("CF_SITE_CONFIG=CREATED_ROOT_ONLY")
    if API_TOKEN.exists():
        require(secure_read(API_TOKEN) == token, "EXISTING_API_TOKEN_DIFFERENT")
        print("CF_API_TOKEN=UNCHANGED_ROOT_ONLY")
    else:
        atomic_new(API_TOKEN, token + "\n")
        print("CF_API_TOKEN=CREATED_ROOT_ONLY")
    print("CF_ENROLLMENT=PASS")
    print("CLOUDFLARED=UNCHANGED")


def systemctl(*args):
    proc = subprocess.run(["systemctl", *args], capture_output=True, text=True,
                          timeout=45, check=False)
    require(proc.returncode == 0, "SYSTEMCTL_FAILED_" + args[0])
    return proc.stdout.strip()


def install_missing_cloudflared():
    """Install only when absent using Cloudflare's signed Debian APT repository."""
    executable = shutil.which("cloudflared")
    if executable:
        print("CLOUDFLARED_PACKAGE=PRESENT")
        return executable
    require(Path("/usr/bin/apt-get").exists() and Path("/usr/bin/dpkg").exists(),
            "CLOUDFLARED_INSTALL_UNSUPPORTED_PLATFORM")
    source = Path("/etc/apt/sources.list.d/dh-cloudflared.list")
    key = Path("/usr/share/keyrings/dh-cloudflared.gpg")
    desired = ("deb [signed-by=/usr/share/keyrings/dh-cloudflared.gpg] "
               "https://pkg.cloudflare.com/cloudflared any main\n")
    if source.exists() or source.is_symlink():
        require(source.is_file() and not source.is_symlink()
                and source.read_text() == desired, "CLOUDFLARED_APT_SOURCE_CONFLICT")
    if key.exists() or key.is_symlink():
        require(key.is_file() and not key.is_symlink() and key.stat().st_uid == 0
                and (key.stat().st_mode & 0o022) == 0, "CLOUDFLARED_APT_KEY_CONFLICT")
    if not key.exists():
        req = urllib.request.Request(
            "https://pkg.cloudflare.com/cloudflare-main.gpg",
            headers={"User-Agent": "DigitalHouses-CloudflareInstaller/1"})
        try:
            with urllib.request.urlopen(req, timeout=20) as response:
                require(response.status == 200, "CLOUDFLARED_APT_KEY_HTTP")
                data = response.read(131073)
        except (OSError, urllib.error.URLError):
            raise Block("CLOUDFLARED_APT_KEY_DOWNLOAD_FAILED") from None
        require(100 < len(data) <= 131072, "CLOUDFLARED_APT_KEY_INVALID")
        # cloudflare-main.gpg is distributed as binary OpenPGP; gpg validates
        # the packet structure before the key is trusted by signed-by APT.
        proc = subprocess.run(["/usr/bin/gpg", "--show-keys", "--batch"],
                              input=data, capture_output=True, timeout=20)
        require(proc.returncode == 0, "CLOUDFLARED_APT_KEY_NOT_OPENPGP")
        atomic_binary(key, data, mode=0o644)
    if not source.exists():
        atomic_new(source, desired, mode=0o644)
    for command in (
        ["/usr/bin/apt-get", "update", "-o", "APT::Update::Error-Mode=any"],
        ["/usr/bin/apt-get", "install", "-y", "--no-install-recommends", "cloudflared"],
    ):
        proc = subprocess.run(command, capture_output=True, text=True,
                              timeout=240, check=False,
                              env={**os.environ, "DEBIAN_FRONTEND": "noninteractive"})
        require(proc.returncode == 0, "CLOUDFLARED_APT_FAILED")
    executable = shutil.which("cloudflared")
    require(executable is not None, "CLOUDFLARED_BINARY_STILL_MISSING")
    print("CLOUDFLARED_PACKAGE=INSTALLED")
    return executable


def pve_matches_config(cfg):
    addresses = subprocess.run(["ip", "-4", "-o", "addr", "show"],
                               capture_output=True, text=True, timeout=10)
    return addresses.returncode == 0 and any(
        part.split("/")[0] == cfg["pve_host"]
        for line in addresses.stdout.splitlines()
        for part in line.split() if "/" in part
    )


def wait_healthy(token, cfg):
    for _ in range(24):
        state = verify_cloudflare(token, cfg, require_old_healthy=False)
        if state == "healthy" and systemctl("is-active", "cloudflared.service") == "active":
            return True
        time.sleep(3)
    return False


def apply():
    # Credential bootstrap is an explicit one-time owner action; it is never
    # inferred from the unprivileged Semaphore environment.
    cfg = json.loads(secure_read(CONFIG))
    require(SITE.fullmatch(cfg.get("site_id", "")), "CONFIG_SITE_INVALID")
    require(cfg["hostname"] == "dh-" + cfg["site_id"] + "-pve." + cfg["zone"],
            "CONFIG_HOST_DRIFT")
    require(pve_matches_config(cfg), "WRONG_PVE_LOCAL_IP")
    token = secure_read(API_TOKEN)
    require(not DROPIN.is_symlink(), "DROPIN_SYMLINK")
    require(not CONNECTOR_TOKEN.is_symlink(), "CONNECTOR_TOKEN_SYMLINK")
    require(not Path("/etc/systemd/system/cloudflared.service").is_symlink(),
            "CLOUDFLARED_UNIT_SYMLINK")
    existing_unit = Path("/etc/systemd/system/cloudflared.service")
    unit_exists = existing_unit.exists()
    preexisting = subprocess.run(["systemctl", "cat", "cloudflared.service"],
                                 capture_output=True, text=True, timeout=15)
    service_exists = preexisting.returncode == 0

    print("CLOUDFLARE_AUDIT=PASS")
    verify_cloudflare(token, cfg, require_old_healthy=bool(
        cfg.get("old_tunnel_id")) and not DROPIN.exists() and service_exists)
    print("CLOUDFLARE_PLAN=" + ("VERIFY_ONLY" if DROPIN.exists()
          else "RECONFIGURE_EXISTING" if service_exists else "INSTALL_NEW"))

    # No package installation, unit edits, token fetch or restart when
    # an already-applied connector is healthy and matches our contract.
    if DROPIN.exists():
        require(service_exists and DROPIN.is_file(), "CLOUDFLARED_UNIT_DRIFT")
        executable = shutil.which("cloudflared")
        require(executable is not None, "CLOUDFLARED_BINARY_MISSING")
        desired = ("[Service]\nExecStart=\nExecStart=" + executable
                   + " --no-autoupdate tunnel run --token-file "
                   + str(CONNECTOR_TOKEN) + "\n")
        require(DROPIN.read_text() == desired, "EXISTING_DROPIN_UNEXPECTED")
        require(bool(secure_read(CONNECTOR_TOKEN)), "CONNECTOR_TOKEN_MISSING")
        if subprocess.run(["systemctl", "is-active", "--quiet", "cloudflared.service"],
                          timeout=10, check=False).returncode != 0:
            systemctl("start", "cloudflared.service")
            require(wait_healthy(token, cfg), "CANONICAL_TUNNEL_RECOVERY_FAILED")
            print("CF_CUTOVER=PASS")
            print("TUNNEL_HEALTH=PASS")
            print("CLOUDFLARE_APPLY=RECOVERED_SERVICE")
            return
        require(wait_healthy(token, cfg), "CANONICAL_TUNNEL_NOT_HEALTHY")
        print("CF_CUTOVER=ALREADY_APPLIED")
        print("TUNNEL_HEALTH=PASS")
        print("CLOUDFLARE_APPLY=UNCHANGED")
        return

    # Fail before writes on unknown preexisting files or service identity.
    require(not DROPIN.exists() and not CONNECTOR_TOKEN.exists(),
            "CLOUDFLARED_STATE_CONFLICT")
    if service_exists:
        old_start = systemctl("show", "-P", "ExecStart", "cloudflared.service")
        require("cloudflared" in old_start and "tunnel" in old_start
                and "--token" in old_start, "UNKNOWN_EXISTING_CONNECTOR_CONFIG")
        require(systemctl("show", "-P", "User", "cloudflared.service") in ("", "root"),
                "SERVICE_NOT_RUNNING_AS_ROOT")
    else:
        require(not unit_exists, "CLOUDFLARED_LOCAL_UNIT_CONFLICT")

    # Keep API preflight and generated credentials before any service changes.
    new_token = api(token, "GET", "/accounts/" + cfg["account_id"]
                    + "/cfd_tunnel/" + cfg["new_tunnel_id"] + "/token")
    require(isinstance(new_token, str) and 80 <= len(new_token) <= 4096,
            "CONNECTOR_TOKEN_UNAVAILABLE")
    executable = install_missing_cloudflared()
    desired = ("[Service]\nExecStart=\nExecStart=" + executable
               + " --no-autoupdate tunnel run --token-file "
               + str(CONNECTOR_TOKEN) + "\n")
    installed_unit = False
    installed_dropin = False
    token_staged = False
    try:
        atomic_new(CONNECTOR_TOKEN, new_token + "\n")
        token_staged = True
        if service_exists:
            atomic_new(DROPIN, desired, mode=0o644)
            installed_dropin = True
        else:
            # Fresh PVE has exactly one cloudflared service.
            body = ("[Unit]\nDescription=Cloudflare Tunnel\n"
                    "Wants=network-online.target\nAfter=network-online.target\n"
                    "[Service]\nType=simple\nExecStart=" + executable
                    + " --no-autoupdate tunnel run --token-file "
                    + str(CONNECTOR_TOKEN)
                    + "\nRestart=always\nRestartSec=5\n"
                    "[Install]\nWantedBy=multi-user.target\n")
            atomic_new(existing_unit, body, mode=0o644)
            installed_unit = True
        systemctl("daemon-reload")
        if installed_unit:
            systemctl("enable", "--now", "cloudflared.service")
        else:
            systemctl("restart", "cloudflared.service")
        require(wait_healthy(token, cfg), "CANONICAL_TUNNEL_HEALTH_TIMEOUT")
        print("CF_CUTOVER=PASS")
        print("TUNNEL_HEALTH=PASS")
        print("OLD_TUNNEL_DELETED=NO")
        print("BROWSER_POST_AUTH_VERIFICATION=PENDING")
        print("CLOUDFLARE_APPLY=CHANGED")
        return
    except Exception:
        if installed_dropin:
            DROPIN.unlink(missing_ok=True)
        if installed_unit:
            try:
                systemctl("disable", "--now", "cloudflared.service")
            except Exception:
                print("CF_ROLLBACK_DISABLE=FAILED")
            existing_unit.unlink(missing_ok=True)
        if installed_dropin or installed_unit:
            try:
                systemctl("daemon-reload")
                if installed_dropin:
                    systemctl("restart", "cloudflared.service")
                print("CF_ROLLBACK=ATTEMPTED")
            except Exception:
                print("CF_ROLLBACK=FAILED_MANUAL_RECOVERY_REQUIRED")
        if token_staged:
            CONNECTOR_TOKEN.unlink(missing_ok=True)
        raise


def main():
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--enroll", action="store_true")
    mode.add_argument("--apply", action="store_true")
    parser.add_argument("--site-id")
    parser.add_argument("--account-id")
    parser.add_argument("--zone-id")
    parser.add_argument("--zone", default="digitalhouses.vip")
    parser.add_argument("--old-tunnel-id")
    parser.add_argument("--old-tunnel-name")
    parser.add_argument("--new-tunnel-id")
    parser.add_argument("--access-app-id")
    parser.add_argument("--access-policy-id")
    parser.add_argument("--pve-host")
    args = parser.parse_args()
    verify_identity()
    if args.enroll:
        for key in ("site_id", "account_id", "zone_id",
                    "new_tunnel_id", "access_app_id", "access_policy_id", "pve_host"):
            require(getattr(args, key), "ENROLLMENT_ARG_REQUIRED_" + key)
        enroll(args)
    else:
        require(not any(getattr(args, key) for key in (
            "site_id", "account_id", "zone_id", "old_tunnel_id", "new_tunnel_id",
            "access_app_id", "access_policy_id", "pve_host")),
            "APPLY_TAKES_NO_SITE_PARAMETERS")
        apply()


if __name__ == "__main__":
    try:
        main()
    except Block as error:
        print("BLOCKED: " + str(error))
        sys.exit(2)
    except Exception:
        print("BLOCKED: UNEXPECTED_ERROR_NO_SECRET_OUTPUT")
        sys.exit(2)
