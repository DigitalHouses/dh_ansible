#!/usr/bin/env python3
"""Reconcile ONE site's HAOS Companion mTLS on an existing remote-managed tunnel.

Runs only as an explicitly commissioned PVE root worker. No site arguments,
no arbitrary URL/command inputs. Changes follow a strict fail-closed order:
certificate -> CA association -> WAF -> exact Access Bypass -> ingress -> DNS.
Never deletes or rewrites another site's certificates, rules, apps or records.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import socket
import ssl
import stat
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.cloudflare.com/client/v4"
SITE_CONFIG = Path("/etc/digitalhouses/cloudflare/site.json")
HAOS_CONFIG = Path("/etc/digitalhouses/cloudflare/haos-mtls.json")
TOKEN_PATH = Path("/root/.config/digitalhouses/cloudflare/api-token.secret")
MATERIAL = Path("/etc/digitalhouses/cloudflare/haos-mtls")
RULE_NAME = "DigitalHouses | HA Companion mTLS | fleet CN guard"
# Adopt the LIVE fleet guard, confirmed by Cloudflare GET on 2026-10-23.
# The RFC2253 DN must consist of exactly CN=<requested hostname>.
# Never rewrite the existing Amir Office WAF rule as part of site onboarding.
EXPRESSION = ('(http.host wildcard "*-ha-mtls.digitalhouses.vip" and '
              '(not cf.tls_client_auth.cert_verified or '
              'cf.tls_client_auth.cert_revoked or '
              'not starts_with(cf.tls_client_auth.cert_subject_dn_rfc2253, "CN=") or '
              'substring(cf.tls_client_auth.cert_subject_dn_rfc2253, 3) '
              'ne lower(http.host)))')
HEX32 = re.compile(r"[a-f0-9]{32}\Z")
SITE = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)+\Z")
CERT_ID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\Z")
changed = False


class Block(Exception):
    pass


def require(value, message):
    if not value:
        raise Block(message)


def root_file(path):
    require(path.is_file() and not path.is_symlink(), "ROOT_FILE_MISSING")
    info = path.stat()
    require(info.st_uid == 0 and stat.S_IMODE(info.st_mode) == 0o600,
            "ROOT_FILE_PERMISSIONS_INVALID")
    return path.read_text(encoding="utf-8")


def guarded_binary(path):
    require(path.is_file() and not path.is_symlink(), "ROOT_BINARY_MISSING")
    info = path.stat()
    require(info.st_uid == 0 and stat.S_IMODE(info.st_mode) == 0o600,
            "ROOT_BINARY_PERMISSIONS_INVALID")
    return path.read_bytes()


def new_file(path, content):
    require(path.parent.is_dir() and not path.parent.is_symlink()
            and path.parent.stat().st_uid == 0
            and stat.S_IMODE(path.parent.stat().st_mode) == 0o700,
            "SECRET_DIRECTORY_PERMISSIONS")
    require(not path.is_symlink() and not path.exists(), "SECRET_FILE_ALREADY_EXISTS")
    data = content.encode() if isinstance(content, str) else content
    descriptor = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL
                         | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(descriptor, "wb") as output:
        output.write(data)
        output.flush()
        os.fsync(output.fileno())
    os.chmod(path, 0o600)


def run(*args, input_data=None):
    result = subprocess.run(args, input=input_data, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, timeout=40, check=False)
    require(result.returncode == 0, "OPENSSL_FAILED")
    return result.stdout


def api(token, method, path, payload=None):
    require(path.startswith("/") and "://" not in path, "API_PATH_INVALID")
    data = json.dumps(payload, separators=(",", ":")).encode() if payload is not None else None
    req = urllib.request.Request(
        API + path, data=data, method=method,
        headers={"Authorization": "Bearer " + token,
                 "Accept": "application/json",
                 "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            response_json = json.load(response)
    except urllib.error.HTTPError as exc:
        raise Block("CF_API_" + method + "_HTTP_" + str(exc.code)) from None
    except (OSError, TimeoutError, ValueError):
        raise Block("CF_API_UNAVAILABLE") from None
    require(isinstance(response_json, dict) and response_json.get("success") is True,
            "CF_API_REJECTED_" + method)
    return response_json.get("result")


def ip_on_host(ip):
    output = subprocess.run(["/usr/sbin/ip", "-4", "-o", "addr", "show"],
                            capture_output=True, text=True, timeout=10)
    return output.returncode == 0 and any(
        part.split("/")[0] == ip for line in output.stdout.splitlines()
        for part in line.split() if "/" in part
    )


def validate_config():
    require(os.geteuid() == 0, "ROOT_REQUIRED")
    require(not MATERIAL.is_symlink() and MATERIAL.is_dir()
            and MATERIAL.stat().st_uid == 0
            and stat.S_IMODE(MATERIAL.stat().st_mode) == 0o700,
            "MATERIAL_DIRECTORY_NOT_COMMISSIONED")
    cf = json.loads(root_file(SITE_CONFIG))
    ha = json.loads(root_file(HAOS_CONFIG))
    require(SITE.fullmatch(str(cf.get("site_id", ""))), "SITE_ID_INVALID")
    require(ha.get("site_id") == cf["site_id"]
            and ha.get("approved_for_publish") is True
            and ha.get("certificate_policy") == "one-per-home-shared-with-residents"
            and ha.get("vmid") >= 100, "HAOS_APPROVAL_DRIFT")
    require(cf.get("zone") == "digitalhouses.vip"
            and all(HEX32.fullmatch(str(cf.get(k, "")))
                    for k in ("account_id", "zone_id"))
            and CERT_ID.fullmatch(str(cf.get("new_tunnel_id", "")))
            and ip_on_host(cf.get("pve_host", "")), "CF_SITE_IDENTITY_DRIFT")
    ip = ipaddress.ip_address(ha["ipv4"])
    require(ip.version == 4 and ip.is_private
            and str(ip).startswith("192.168.")
            and ha.get("port") == 80, "HAOS_ORIGIN_UNEXPECTED")
    require(ha.get("hostname") == cf["site_id"] + "-pve-ha-mtls." + cf["zone"],
            "HAOS_HOSTNAME_DRIFT")
    return cf, ha


def certificate_inventory(token, zone):
    found = []
    for page in range(1, 4):
        part = api(token, "GET", "/zones/" + zone + "/client_certificates?"
                   + urllib.parse.urlencode({"status": "all", "per_page": 50, "page": page}))
        require(isinstance(part, list), "CLIENT_CERT_LIST_INVALID")
        found.extend(part)
        if len(part) < 50:
            return found
    raise Block("CLIENT_CERT_PAGINATION_LIMIT")


def pubkey_of_cert(content):
    return run("/usr/bin/openssl", "x509", "-pubkey", "-noout",
               input_data=content.encode()).strip()


def pubkey_of_key(path):
    return run("/usr/bin/openssl", "pkey", "-in", str(path),
               "-pubout").strip()


def certificate(token, cf, ha):
    """Issue once only. Retain one household identity; never mint a duplicate."""
    global changed
    host = ha["hostname"]
    key = MATERIAL / "client.key"
    csr = MATERIAL / "client.csr"
    crt = MATERIAL / "client.crt"
    certificate_id = MATERIAL / "certificate-id"
    all_certs = certificate_inventory(token, cf["zone_id"])
    matching = [x for x in all_certs
                if x.get("status") == "active" and x.get("common_name") == host]
    require(len(matching) <= 1, "MULTIPLE_ACTIVE_HOUSEHOLD_CERTIFICATES")
    if not key.exists():
        require(not csr.exists() and not crt.exists() and not certificate_id.exists()
                and not matching, "UNRECOVERABLE_EXISTING_CERTIFICATE")
        generated_key = run("/usr/bin/openssl", "genpkey", "-algorithm", "RSA",
                            "-pkeyopt", "rsa_keygen_bits:2048")
        new_file(key, generated_key)
        csr_data = run("/usr/bin/openssl", "req", "-new",
                       "-key", str(key), "-subj", "/CN=" + host)
        new_file(csr, csr_data)
        changed = True
    require(key.exists() and csr.exists(), "CSR_OR_KEY_MISSING")
    root_file(key)
    root_file(csr)
    if matching:
        remote = matching[0]
        require(CERT_ID.fullmatch(str(remote.get("id", ""))), "CERT_ID_INVALID")
        if not isinstance(remote.get("certificate"), str):
            remote = api(token, "GET",
                         "/zones/" + cf["zone_id"] + "/client_certificates/" + remote["id"])
        require(isinstance(remote, dict) and isinstance(remote.get("certificate"), str),
                "CANNOT_RECOVER_CERTIFICATE_PUBLIC_PEM")
    else:
        existing = [x for x in all_certs
                    if x.get("common_name") == host and x.get("status") not in ("revoked", "expired")]
        require(not existing, "CLIENT_CERT_STATE_AMBIGUOUS")
        remote = api(token, "POST", "/zones/" + cf["zone_id"] + "/client_certificates",
                     {"csr": root_file(csr), "validity_days": 365})
        require(isinstance(remote, dict) and remote.get("status") == "active",
                "CERT_ISSUANCE_NOT_ACTIVE")
        changed = True
    require(CERT_ID.fullmatch(str(remote.get("id", ""))), "CERT_ID_INVALID")
    ca = remote.get("certificate_authority") or {}
    require(isinstance(ca, dict) and "Cloudflare Managed CA" in str(ca.get("name", "")),
            "CERTIFICATE_ISSUER_NOT_CLOUDFLARE_MANAGED_CA")
    pem = remote["certificate"]
    require(pubkey_of_cert(pem) == pubkey_of_key(key), "CERTIFICATE_KEY_MISMATCH")
    subject = run("/usr/bin/openssl", "x509", "-noout", "-subject",
                  "-nameopt", "RFC2253", input_data=pem.encode()).decode().strip()
    require(subject == "subject=CN=" + host, "CERTIFICATE_CN_MISMATCH")
    run("/usr/bin/openssl", "x509", "-checkend", "0", "-noout",
        input_data=pem.encode())
    if not crt.exists():
        new_file(crt, pem)
    else:
        require(root_file(crt) == pem, "CERTIFICATE_PEM_DRIFT")
    if not certificate_id.exists():
        new_file(certificate_id, remote["id"] + "\n")
    else:
        require(root_file(certificate_id).strip() == remote["id"], "CERTIFICATE_ID_DRIFT")
    password_file = MATERIAL / "p12-password"
    modern = MATERIAL / "ha-companion.p12"
    apple = MATERIAL / "ha-companion-ipados.p12"
    if not password_file.exists():
        require(not modern.exists() and not apple.exists(), "P12_PASSWORD_MISSING")
        new_file(password_file, secrets.token_urlsafe(36) + "\n")
        changed = True
    root_file(password_file)
    if not modern.exists():
        result = run("/usr/bin/openssl", "pkcs12", "-export",
                     "-inkey", str(key), "-in", str(crt),
                     "-name", host, "-passout", "file:" + str(password_file))
        # The CLI needs -out to write a PKCS#12 file; stdout is supported
        # on OpenSSL 3 but explicitly verify the output before storing it.
        require(len(result) > 1500, "MODERN_PKCS12_EMPTY")
        new_file(modern, result)
        changed = True
    if not apple.exists():
        result = run("/usr/bin/openssl", "pkcs12", "-export", "-legacy",
                     "-keypbe", "PBE-SHA1-3DES", "-certpbe", "PBE-SHA1-3DES",
                     "-macalg", "sha1", "-inkey", str(key), "-in", str(crt),
                     "-name", host, "-passout", "file:" + str(password_file))
        require(len(result) > 1500, "APPLE_PKCS12_EMPTY")
        new_file(apple, result)
        changed = True
    for path in (modern, apple):
        guarded_binary(path)  # encrypted PKCS#12 bytes are never logged.
        run("/usr/bin/openssl", "pkcs12", "-info", "-noout",
            "-in", str(path), "-passin", "file:" + str(password_file))
    return pem, key


def guard(token, cf, ha):
    """Make the shared wildcard block rule effective before any HAOS route."""
    global changed
    zone = cf["zone_id"]
    phase = "http_request_firewall_custom"
    ruleset = api(token, "GET",
                  "/zones/" + zone + "/rulesets/phases/" + phase + "/entrypoint")
    require(isinstance(ruleset, dict) and isinstance(ruleset.get("rules"), list)
            and isinstance(ruleset.get("id"), str), "WAF_ENTRYPOINT_ABSENT")
    rules = ruleset["rules"]
    matches = [r for r in rules if r.get("description") == RULE_NAME]
    require(len(matches) <= 1, "DUPLICATE_FLEET_WAF")
    if not matches:
        require(len(rules) < 5, "WAF_CUSTOM_RULES_QUOTA")
        definition = {"action": "block", "enabled": True,
                      "description": RULE_NAME, "expression": EXPRESSION}
        # Position before any skip/challenge or other WAF rule.
        if rules:
            require(isinstance(rules[0].get("id"), str), "WAF_FIRST_RULE_ID_MISSING")
            definition["position"] = {"before": rules[0]["id"]}
        api(token, "POST", "/zones/" + zone + "/rulesets/" + ruleset["id"] + "/rules",
            definition)
        changed = True
        ruleset = api(token, "GET",
                      "/zones/" + zone + "/rulesets/phases/" + phase + "/entrypoint")
        rules = ruleset["rules"]
        matches = [r for r in rules if r.get("description") == RULE_NAME]
    require(len(matches) == 1 and matches[0].get("enabled") is True
            and matches[0].get("action") == "block"
            and matches[0].get("expression") == EXPRESSION,
            "FLEET_WAF_EXPRESSION_DRIFT")
    # A pre-existing skip rule before the guard can defeat mTLS enforcement.
    ix = rules.index(matches[0])
    require(not any(r.get("action") == "skip" for r in rules[:ix]),
            "WAF_SKIP_PRECEDES_MTLS_GUARD")


def ca_association(token, cf, ha):
    global changed
    path = "/zones/" + cf["zone_id"] + "/certificate_authorities/hostname_associations"
    result = api(token, "GET", path)
    hosts = (result or {}).get("hostnames")
    require(isinstance(hosts, list)
            and all(isinstance(h, str) for h in hosts)
            and len(set(hosts)) == len(hosts), "CA_ASSOCIATION_LIST_INVALID")
    if ha["hostname"] not in hosts:
        api(token, "PUT", path, {"hostnames": hosts + [ha["hostname"]]})
        changed = True
        readback = api(token, "GET", path)
        require(set(readback.get("hostnames", [])) == set(hosts + [ha["hostname"]]),
                "CA_ASSOCIATION_NOT_VERIFIED")


def access_policy_is_bypass(token, cf, app):
    refs = app.get("policies") or []
    for entry in refs:
        identifier = entry if isinstance(entry, str) else (
            entry.get("id") if isinstance(entry, dict) else None)
        if not identifier:
            continue
        policy = api(token, "GET", "/accounts/" + cf["account_id"]
                     + "/access/policies/" + identifier)
        if (policy.get("decision") == "bypass"
                and policy.get("include") == [{"everyone": {}}]):
            return True
    return False


def ensure_access(token, cf, ha):
    """Exact-host Bypass only. Wildcard administrator policy is untouched."""
    global changed
    base = "/accounts/" + cf["account_id"]
    apps = api(token, "GET", base + "/access/apps?per_page=100")
    require(isinstance(apps, list), "ACCESS_APPS_LIST_INVALID")
    matching = [a for a in apps if a.get("domain") == ha["hostname"]]
    require(len(matching) <= 1, "ACCESS_EXACT_APP_COLLISION")
    if not matching:
        policy_name = "DigitalHouses HAOS mTLS Bypass " + cf["site_id"]
        policies = api(token, "GET", base + "/access/policies?per_page=100")
        require(isinstance(policies, list), "ACCESS_POLICY_LIST_INVALID")
        named = [p for p in policies if p.get("name") == policy_name]
        require(len(named) <= 1, "ACCESS_POLICY_NAME_COLLISION")
        if named:
            policy = named[0]
        else:
            policy = api(token, "POST", base + "/access/policies",
                         {"name": policy_name, "decision": "bypass",
                          "include": [{"everyone": {}}]})
            changed = True
        require(isinstance(policy, dict) and isinstance(policy.get("id"), str)
                and policy.get("decision") == "bypass"
                and policy.get("include") == [{"everyone": {}}],
                "ACCESS_POLICY_NOT_EXACT_BYPASS")
        app = api(token, "POST", base + "/access/apps",
                  {"name": "DigitalHouses HAOS mTLS " + cf["site_id"],
                   "type": "self_hosted", "domain": ha["hostname"],
                   "session_duration": "24h", "policies": [{"id": policy["id"]}]})
        require(isinstance(app, dict) and isinstance(app.get("id"), str),
                "ACCESS_APP_CREATE_FAILED")
        changed = True
    else:
        app = matching[0]
    require(app.get("type") == "self_hosted"
            and app.get("domain") == ha["hostname"]
            and isinstance(app.get("id"), str), "ACCESS_APP_IDENTITY_DRIFT")
    readback = api(token, "GET", base + "/access/apps/" + app["id"])
    require(readback.get("domain") == ha["hostname"]
            and access_policy_is_bypass(token, cf, readback),
            "ACCESS_EXACT_BYPASS_NOT_VERIFIED")


def tunnel(token, cf, ha):
    """Append exactly one route before fallback, preserve ALL existing ingress."""
    global changed
    prefix = "/accounts/" + cf["account_id"] + "/cfd_tunnel/"
    info = api(token, "GET", prefix + cf["new_tunnel_id"])
    require(info.get("name") == cf["site_id"] and info.get("config_src") == "cloudflare",
            "CANONICAL_TUNNEL_DRIFT")
    path = prefix + cf["new_tunnel_id"] + "/configurations"
    obj = api(token, "GET", path)
    config = obj.get("config") if isinstance(obj, dict) else None
    require(isinstance(config, dict) and isinstance(config.get("ingress"), list),
            "TUNNEL_CONFIGURATION_INVALID")
    ingress = config["ingress"]
    require(len(ingress) >= 2 and ingress[-1].get("service") == "http_status:404"
            and "hostname" not in ingress[-1], "TUNNEL_CATCHALL_DRIFT")
    current = [x for x in ingress if x.get("hostname") == ha["hostname"]]
    service = "http://" + ha["ipv4"] + ":" + str(ha["port"])
    require(len(current) <= 1 and (not current or current[0].get("service") == service),
            "HAOS_TUNNEL_ROUTE_COLLISION")
    if not current:
        updated = dict(config)
        updated["ingress"] = ingress[:-1] + [
            {"hostname": ha["hostname"], "service": service}
        ] + ingress[-1:]
        api(token, "PUT", path, {"config": updated})
        changed = True
    check = api(token, "GET", path)
    check_ingress = (check.get("config") or {}).get("ingress")
    require(isinstance(check_ingress, list)
            and len([x for x in check_ingress
                     if x.get("hostname") == ha["hostname"] and
                     x.get("service") == service]) == 1,
            "HAOS_INGRESS_READBACK_FAILED")


def dns(token, cf, ha):
    global changed
    path = "/zones/" + cf["zone_id"] + "/dns_records"
    records = api(token, "GET", path + "?" + urllib.parse.urlencode(
        {"name": ha["hostname"]}))
    require(isinstance(records, list) and len(records) <= 1,
            "HAOS_DNS_COLLISION")
    target = cf["new_tunnel_id"] + ".cfargotunnel.com"
    if not records:
        api(token, "POST", path, {
            "type": "CNAME", "name": ha["hostname"],
            "content": target, "proxied": True, "ttl": 1,
        })
        changed = True
    readback = api(token, "GET", path + "?" + urllib.parse.urlencode(
        {"name": ha["hostname"]}))
    require(isinstance(readback, list) and len(readback) == 1
            and readback[0].get("type") == "CNAME"
            and readback[0].get("content") == target
            and readback[0].get("proxied") is True,
            "HAOS_DNS_READBACK_FAILED")


def public_check(ha, key, cert):
    """No client certificate: 403. Valid certificate: HA API returns 401."""
    url = "https://" + ha["hostname"] + "/api/"
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            anonymous_status = response.status
    except urllib.error.HTTPError as exc:
        anonymous_status = exc.code
    require(anonymous_status == 403, "ANONYMOUS_NOT_BLOCKED")
    context = ssl.create_default_context()
    context.load_cert_chain(str(cert), str(key))
    try:
        with urllib.request.urlopen(req, timeout=20, context=context) as response:
            authorized_status = response.status
    except urllib.error.HTTPError as exc:
        authorized_status = exc.code
    require(authorized_status == 401, "VALID_CERT_NOT_REACHING_HA")


def main():
    require(sys.argv == [sys.argv[0], "--apply"], "FIXED_APPLY_ONLY")
    cf, ha = validate_config()
    token = root_file(TOKEN_PATH).strip()
    require(16 <= len(token) <= 512, "CF_TOKEN_INVALID")
    zone = api(token, "GET", "/zones/" + cf["zone_id"])
    require(zone.get("name") == cf["zone"]
            and (zone.get("account") or {}).get("id") == cf["account_id"],
            "ZONE_ACCOUNT_MISMATCH")
    # A private origin must be alive BEFORE doing any Cloudflare work.
    request = urllib.request.Request("http://" + ha["ipv4"] + ":80/api/")
    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            status = response.status
    except urllib.error.HTTPError as exc:
        status = exc.code
    require(status == 401, "LOCAL_HAOS_NOT_READY")
    certificate(token, cf, ha)
    ca_association(token, cf, ha)
    guard(token, cf, ha)
    ensure_access(token, cf, ha)
    tunnel(token, cf, ha)
    dns(token, cf, ha)
    public_check(ha, MATERIAL / "client.key", MATERIAL / "client.crt")
    print("HAOS_MTLS=PASS")
    print("HAOS_MTLS_HOSTNAME=" + ha["hostname"])
    print("HAOS_MTLS_ORIGIN=http://" + ha["ipv4"] + ":" + str(ha["port"]))
    print("HAOS_MTLS_RESULT=" + ("CHANGED" if changed else "UNCHANGED"))
    print("CERTIFICATE_POLICY=ONE_PER_SITE")
    print("PUBLIC_WITHOUT_CERT=403")
    print("PUBLIC_WITH_CERT_HA_API=401")
    print("LEGACY_ROUTES=UNCHANGED")


if __name__ == "__main__":
    try:
        main()
    except Block as exc:
        print("BLOCKED: " + str(exc))
        sys.exit(2)
    except Exception:
        print("BLOCKED: HAOS_MTLS_INTERNAL_FAILURE")
        sys.exit(2)
