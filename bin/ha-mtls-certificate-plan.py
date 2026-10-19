#!/usr/bin/env python3
"""Fleet HA Companion client-certificate preflight. No Cloudflare writes.

--plan reads Cloudflare state with a root-only site-runner token.
--csr-self-test generates a *disposable* CN-only CSR in /dev/shm (tmpfs).
There is deliberately no issue, revoke, publish, Vault-write or WAF-apply path.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import ipaddress
import os
from pathlib import Path
import re
import socket
import stat
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request

import yaml

ROOT = Path(__file__).resolve().parents[1]
FLEET_ROOT = Path(os.environ.get("DH_FLEET_DETAIL_DIR", "/var/lib/ansimble/fleet-detail"))
TOKEN_PATH = Path("/root/.config/digitalhouses/cloudflare/api-token.secret")
API = "https://api.cloudflare.com/client/v4"
TMPFS = Path("/dev/shm")
FQDN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*-ha-mtls\.digitalhouses\.vip$")
SITE_ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
ZONE_HOST = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*\.digitalhouses\.vip$")
RULE_NAME = "DigitalHouses | HA Companion mTLS | fleet CN guard"
PHASE = "http_request_firewall_custom"
LIMIT = 100


class Block(Exception):
    """Fail closed, without disclosing secrets or API response bodies."""


def intent(site: str, root: Path = FLEET_ROOT) -> dict:
    if not SITE_ID.fullmatch(site):
        raise Block("INVALID_SITE_ID")
    inventory = root / "inventories" / site / "cloudflare_ha_mtls.yml"
    if not inventory.is_file():
        raise Block("SITE_INVENTORY_NOT_FOUND")
    obj = yaml.safe_load(inventory.read_text(encoding="utf-8"))
    state = obj.get("cloudflare_ha_mtls") if isinstance(obj, dict) else None
    if not isinstance(state, dict) or state.get("site_id") != site:
        raise Block("SITE_INVENTORY_INVALID")
    if (state.get("enabled") is not False
            or state.get("live_writes_authorized") is not False
            or state.get("waf", {}).get("apply_authorized") is not False):
        raise Block("MUTATIONS_MUST_REMAIN_DISABLED")
    host = state.get("hostname")
    if not isinstance(host, str) or not FQDN.fullmatch(host):
        raise Block("INVALID_CLIENT_HOSTNAME")
    if state.get("cf_zone_name") != "digitalhouses.vip":
        raise Block("UNEXPECTED_ZONE")
    if not re.fullmatch(r"[a-f0-9]{32}", str(state.get("cf_zone_id"))):
        raise Block("INVALID_ZONE_ID")
    if not re.fullmatch(r"[a-f0-9]{32}", str(state.get("cf_account_id"))):
        raise Block("INVALID_ACCOUNT_ID")
    if not re.fullmatch(r"[0-9a-f-]{36}", str(state.get("existing_tunnel_id"))):
        raise Block("INVALID_TUNNEL_ID")
    if state.get("cert_issue_strategy") != "csr-generated-locally":
        raise Block("UNEXPECTED_CERT_ISSUE_STRATEGY")
    if state.get("cert_subject") != "CN-only-exact-hostname":
        raise Block("UNEXPECTED_CERT_SUBJECT")
    if state.get("cert_key_storage") != "encrypted-fleet-vault":
        raise Block("UNEXPECTED_SECRET_STORAGE")
    if state.get("certificate_policy") != "one-per-home-shared-with-residents":
        raise Block("UNEXPECTED_CERT_POLICY")
    if state.get("trust_scope") != "Cloudflare-managed-CA":
        raise Block("UNEXPECTED_CERT_CA")
    if state.get("waf", {}).get("rule_name") != RULE_NAME:
        raise Block("UNEXPECTED_WAF_GUARD")
    if state.get("waf", {}).get("target_pattern") != "*-ha-mtls.digitalhouses.vip":
        raise Block("UNEXPECTED_WAF_HOST_PATTERN")
    legacy = state.get("legacy_guard")
    if (not isinstance(legacy, dict)
            or not ZONE_HOST.fullmatch(str(legacy.get("waf_hostname", "")))
            or not ZONE_HOST.fullmatch(str(legacy.get("ca_hostname", "")))
            or legacy["waf_hostname"] == host
            or legacy["ca_hostname"] == host):
        raise Block("LEGACY_GUARD_INVALID")
    if not str(state.get("p12_destination", "")).startswith("/mnt/"):
        raise Block("UNEXPECTED_P12_DESTINATION")
    days = state.get("cert_validity_days", 365)
    if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= 3650:
        raise Block("INVALID_CERT_VALIDITY_DAYS")
    return state


def private_nas_ipv4(value) -> bool:
    """Require an RFC1918 unicast address, not merely is_private.

    ipaddress.is_private also includes documentation and other special-use
    networks, which are not valid configured LAN NAS endpoints.
    """
    try:
        octets = tuple(int(i) for i in str(ipaddress.IPv4Address(value)).split("."))
    except (ValueError, ipaddress.AddressValueError):
        return False
    return (octets[0] == 10
            or (octets[0] == 172 and 16 <= octets[1] <= 31)
            or (octets[0] == 192 and octets[1] == 168))


def accepted_issued_certificate(site: str, root: Path = FLEET_ROOT) -> dict:
    """Read pinned, non-secret identity metadata from private Fleet Detail.

    No Vault decryption, Git modification, Cloudflare request or TrueNAS I/O.
    """
    state = intent(site, root=root)
    recovery = state.get("certificate_recovery")
    bundle = state.get("p12_bundle")
    if not isinstance(recovery, dict) or not isinstance(bundle, dict):
        raise Block("P12_RECOVERY_POLICY_MISSING")
    for key in ("vault_sha256", "certificate_sha256"):
        if not re.fullmatch(r"[0-9a-f]{64}", str(recovery.get(key, ""))):
            raise Block("P12_RECOVERY_SHA_INVALID")
    if not re.fullmatch(r"[0-9a-f]{32}", str(recovery.get("generation_id", ""))):
        raise Block("P12_RECOVERY_GENERATION_INVALID")
    if not re.fullmatch(
        r"[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}",
        str(recovery.get("certificate_id", "")),
    ):
        raise Block("P12_RECOVERY_CERTIFICATE_ID_INVALID")
    size = recovery.get("published_p12_size_bytes")
    if isinstance(size, bool) or not isinstance(size, int) or not 1800 <= size <= 10000:
        raise Block("P12_RECOVERY_SIZE_INVALID")
    uid, gid = bundle.get("filebrowser_uid"), bundle.get("filebrowser_gid")
    if any(isinstance(n, bool) or not isinstance(n, int) or n < 1000
           for n in (uid, gid)):
        raise Block("P12_RECOVERY_OWNER_INVALID")
    if (bundle.get("layout") != "dual-pkcs12-with-password-sidecar"
            or bundle.get("output_directory_mode") != "0700"
            or bundle.get("output_file_mode") != "0600"
            or bundle.get("filename_scheme") != "host-prefix-and-generation8"
            or bundle.get("publish_policy") != "no-overwrite-idempotent"):
        raise Block("P12_RECOVERY_BUNDLE_POLICY_DRIFT")
    return {
        **recovery,
        "hostname": state["hostname"],
        "p12_filename": state["hostname"].removesuffix(".digitalhouses.vip") + "-" + recovery["generation_id"][:8] + ".p12",
        "p12_destination": state["p12_destination"],
        "filebrowser_uid": uid,
        "filebrowser_gid": gid,
    }


def token_for_site(site: str) -> str:
    if (not SITE_ID.fullmatch(site) or os.geteuid() != 0
            or socket.gethostname() != "ansimble-" + site):
        raise Block("REQUIRES_SITE_RUNNER_ROOT")
    if (TOKEN_PATH.is_symlink() or not TOKEN_PATH.is_file()
            or TOKEN_PATH.parent.is_symlink()):
        raise Block("CF_TOKEN_MISSING_OR_LINKED")
    for path, mode in ((TOKEN_PATH, 0o600), (TOKEN_PATH.parent, 0o700)):
        st = path.stat()
        if st.st_uid != 0 or stat.S_IMODE(st.st_mode) != mode:
            raise Block("CF_TOKEN_NOT_ROOT_ONLY")
    token = TOKEN_PATH.read_text(encoding="ascii")
    if not 16 <= len(token) <= 512 or token != token.strip():
        raise Block("CF_TOKEN_INVALID")
    return token


class ReadOnlyCloudflare:
    def __init__(self, token: str):
        self._token = token

    def get(self, path: str):
        if not path.startswith("/") or "://" in path:
            raise Block("INVALID_CF_API_PATH")
        request = urllib.request.Request(
            API + path, method="GET",
            headers={"Authorization": "Bearer " + self._token, "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as resp:
                obj = json.load(resp)
        except urllib.error.HTTPError as exc:
            raise Block(f"CF_GET_HTTP_{exc.code}") from None
        except (OSError, ValueError, TimeoutError):
            raise Block("CF_GET_TRANSPORT_OR_JSON_ERROR") from None
        if not isinstance(obj, dict) or obj.get("success") is not True:
            raise Block("CF_GET_NOT_SUCCESSFUL")
        return obj.get("result")


def all_certificates(client, zone: str):
    results = []
    for page in range(1, 4):
        obj = client.get(
            f"/zones/{zone}/client_certificates?status=all&per_page=50&page={page}"
        )
        if not isinstance(obj, list):
            raise Block("CLIENT_CERT_LIST_INVALID")
        results.extend(obj)
        if len(obj) < 50:
            return results
    raise Block("CLIENT_CERT_PAGINATION_UNEXPECTED")


def build_plan(state: dict, client) -> dict:
    """Read-only; return safe metadata only. Refuse drift and quota exhaustion."""
    zone = state["cf_zone_id"]
    account = state["cf_account_id"]
    host = state["hostname"]
    z = client.get(f"/zones/{zone}")
    if (not isinstance(z, dict) or z.get("name") != state["cf_zone_name"]
            or (z.get("account") or {}).get("id") != account
            or "Free" not in str((z.get("plan") or {}).get("name"))):
        raise Block("ZONE_ACCOUNT_OR_PLAN_CHANGED")

    tunnel_id = state["existing_tunnel_id"]
    tunnel = client.get(f"/accounts/{account}/cfd_tunnel/{tunnel_id}")
    if (not isinstance(tunnel, dict)
            or tunnel.get("name") != state["existing_tunnel_name"]
            or tunnel.get("config_src") != "cloudflare"):
        raise Block("TUNNEL_IDENTITY_CHANGED")
    conf = client.get(f"/accounts/{account}/cfd_tunnel/{tunnel_id}/configurations")
    ingress = (conf or {}).get("config", {}).get("ingress")
    if not isinstance(ingress, list):
        raise Block("TUNNEL_INGRESS_INVALID")
    existing = [x for x in ingress if x.get("hostname") == state["existing_ha_hostname"]]
    proposed = [x for x in ingress if x.get("hostname") == host]
    origin = state["existing_ha_origin"]
    if len(existing) != 1 or existing[0].get("service") != origin:
        raise Block("PRODUCTION_HA_ROUTE_CHANGED")
    if len(proposed) > 1 or (proposed and proposed[0].get("service") != origin):
        raise Block("MTLS_ROUTE_COLLISION")

    waf = client.get(f"/zones/{zone}/rulesets/phases/{PHASE}/entrypoint")
    rules = (waf or {}).get("rules")
    if not isinstance(rules, list):
        raise Block("WAF_ENTRYPOINT_INVALID")
    legacy = state["legacy_guard"]
    home = [r for r in rules if
            r.get("action") == "block" and r.get("enabled") is True
            and r.get("expression") ==
            '(http.host eq "' + legacy["waf_hostname"] + '" and '
            '(not cf.tls_client_auth.cert_verified or cf.tls_client_auth.cert_revoked))']
    if len(home) != 1:
        raise Block("LEGACY_HOME_MTLS_WAF_CHANGED")
    fleet = [r for r in rules if r.get("description") == RULE_NAME]
    if len(fleet) > 1:
        raise Block("DUPLICATE_FLEET_RULE")
    if len(rules) >= 5 and not fleet:
        raise Block("WAF_QUOTA_EXHAUSTED")

    ca = client.get(f"/zones/{zone}/certificate_authorities/hostname_associations")
    hosts = ca.get("hostnames") if isinstance(ca, dict) else None
    if not isinstance(hosts, list) or legacy["ca_hostname"] not in hosts:
        raise Block("LEGACY_CA_ASSOCIATION_CHANGED")

    certs = all_certificates(client, zone)
    active = [c for c in certs if c.get("status") == "active"]
    matching = [c for c in active if c.get("common_name") == host]
    if len(matching) > 2 or len(active) > LIMIT:
        raise Block("CERT_IDENTITY_OR_QUOTA_DRIFT")
    if not matching and len(active) >= LIMIT:
        raise Block("CLIENT_CERT_QUOTA_EXHAUSTED")
    next_action = "ISSUE_AFTER_APPROVAL" if not matching else (
        "ROTATION_IN_PROGRESS" if len(matching) == 2 else "NOOP_CERT_EXISTS"
    )
    return {
        "site": state["site_id"],
        "hostname": host,
        "csr_subject": "CN=" + host,
        "key_algorithm": "RSA-2048",
        "validity_days": state.get("cert_validity_days", 365),
        "legacy_waf_preserved": True,
        "legacy_ca_preserved": True,
        "waf_rules": len(rules),
        "fleet_waf_present": bool(fleet),
        "ca_hostname_associated": host in hosts,
        "mtls_tunnel_route_present": bool(proposed),
        "active_certificates": len(active),
        "home_active_certificates": len(matching),
        "next_action": next_action,
        "mutations": False,
    }


@contextmanager
def disposable_csr(host: str):
    """Create private key in tmpfs; caller must not persist/print it.

    The future issuer must encrypt and commit the authoritative secret before
    any CF certificate issuance. This self-test discards its key unconditionally.
    """
    if not FQDN.fullmatch(host):
        raise Block("CSR_CN_INVALID")
    fs = subprocess.run(
        ["findmnt", "-T", str(TMPFS), "-n", "-o", "FSTYPE"],
        capture_output=True, text=True, check=False,
    )
    if fs.returncode != 0 or fs.stdout.strip() != "tmpfs":
        raise Block("CSR_STAGING_NOT_TMPFS")
    with tempfile.TemporaryDirectory(prefix="dh-mtls-csr-", dir=TMPFS) as dirname:
        directory = Path(dirname)
        directory.chmod(0o700)
        key, csr = directory / "private.key", directory / "request.csr"
        cmd = [
            "openssl", "req", "-new", "-newkey", "rsa:2048", "-noenc",
            "-sha256", "-config", "/dev/null", "-subj", "/CN=" + host,
            "-keyout", str(key), "-out", str(csr),
        ]
        created = subprocess.run(cmd, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, check=False)
        if created.returncode != 0:
            raise Block("CSR_GENERATION_FAILED")
        key.chmod(0o600)
        csr.chmod(0o600)
        subject = subprocess.run(
            ["openssl", "req", "-in", str(csr), "-noout", "-subject",
             "-nameopt", "RFC2253"], capture_output=True, text=True, check=False,
        )
        if subject.returncode != 0 or subject.stdout.strip() != "subject=CN=" + host:
            raise Block("CSR_SUBJECT_NOT_EXACT_CN")
        verified = subprocess.run(
            ["openssl", "req", "-in", str(csr), "-noout", "-verify"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
        )
        if verified.returncode != 0:
            raise Block("CSR_SIGNATURE_INVALID")
        pubkey = subprocess.run(
            ["openssl", "pkey", "-in", str(key), "-pubout"],
            capture_output=True, check=False,
        )
        csr_pubkey = subprocess.run(
            ["openssl", "req", "-in", str(csr), "-pubkey", "-noout"],
            capture_output=True, check=False,
        )
        if (pubkey.returncode or csr_pubkey.returncode
                or pubkey.stdout != csr_pubkey.stdout):
            raise Block("CSR_KEY_MISMATCH")
        yield key, csr


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--site", required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--plan", action="store_true")
    mode.add_argument("--csr-self-test", action="store_true")
    args = parser.parse_args()
    state = intent(args.site)
    print("MODE=" + ("READ_ONLY_PLAN" if args.plan else "TMPFS_DISPOSABLE_CSR_TEST"))
    print("SITE=" + args.site)
    if args.plan:
        client = ReadOnlyCloudflare(token_for_site(args.site))
        plan = build_plan(state, client)
        for name, value in plan.items():
            print(str(name).upper() + "=" + str(value))
    else:
        with disposable_csr(state["hostname"]) as (key, csr):
            if not key.is_file() or not csr.is_file():
                raise Block("CSR_SELF_TEST_FILES_MISSING")
            print("CSR_CN=CN=" + state["hostname"])
            print("CSR_RSA_2048=PASS")
            print("CSR_TMPFS=PASS")
        print("CSR_TEMPORARY_MATERIAL_DISCARDED=PASS")
    print("CLOUDFLARE_MUTATIONS=NONE")
    print("VAULT_MUTATIONS=NONE")


if __name__ == "__main__":
    try:
        main()
    except (Block, OSError, ValueError) as err:
        print("BLOCK=" + (str(err) if isinstance(err, Block) else type(err).__name__),
              file=sys.stderr)
        sys.exit(2)
    finally:
        print("=== DONE ===")
        print("=== TERMINAL STAYS OPEN ===")
