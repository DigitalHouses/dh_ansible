#!/usr/bin/env python3
"""Read-only Cloudflare account and Tunnel/Access API visibility probe.

Never tests write permissions by mutation. Credentials only via stdin;
fixed-origin HTTPS GET, no redirects/proxies, bounded response, safe output.
"""
import json
import re
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.cloudflare.com/client/v4"
ID = re.compile(r"^[0-9a-f]{32}$")
SITE = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)+$")
MAX_BYTES = 262144


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def get(opener, token, path):
    assert path.startswith("/") and "://" not in path
    request = urllib.request.Request(
        API + path, method="GET",
        headers={"Authorization": "Bearer " + token, "Accept": "application/json"}
    )
    try:
        with opener.open(request, timeout=10) as response:
            data = response.read(MAX_BYTES + 1)
            if response.status != 200 or len(data) > MAX_BYTES:
                return "API_UNAVAILABLE", None
        payload = json.loads(data)
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            return "READ_DENIED", None
        if exc.code == 429:
            return "RATE_LIMITED", None
        return "API_UNAVAILABLE", None
    except (OSError, TimeoutError, urllib.error.URLError, ValueError, TypeError):
        return "UNREACHABLE", None
    if not isinstance(payload, dict) or payload.get("success") is not True:
        return "API_UNAVAILABLE", None
    return "READ_CONFIRMED", payload.get("result")


def empty():
    return {
        "overall": "INCONCLUSIVE", "account_id": "",
        "zone_read": "NOT_CHECKED", "tunnel_read": "NOT_CHECKED",
        "tunnel_present": "UNKNOWN", "access_read": "NOT_CHECKED",
        "access_app_present": "UNKNOWN", "write": "NOT_VERIFIED"
    }


def audit(token, site, opener=None):
    result = empty()
    if not (isinstance(token, str) and 20 <= len(token) <= 8192 and
            token.isascii() and "\r" not in token and "\n" not in token and
            isinstance(site, str) and SITE.fullmatch(site)):
        result["zone_read"] = "INVALID_INPUT"
        return result
    if opener is None:
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            urllib.request.HTTPSHandler(context=ssl.create_default_context()),
            NoRedirect()
        )
    # Zone provides the *identity* of the account. No unrelated-account guess.
    zone = "digitalhouses.vip"
    status, zones = get(opener, token, "/zones?" + urllib.parse.urlencode(
        {"name": zone, "per_page": 5}))
    result["zone_read"] = status
    if status != "READ_CONFIRMED" or not isinstance(zones, list):
        return result
    matches = [x for x in zones if isinstance(x, dict) and x.get("name") == zone]
    if len(matches) != 1:
        result["zone_read"] = "ZONE_NOT_UNIQUE"
        return result
    owner = matches[0].get("account")
    account_id = owner.get("id") if isinstance(owner, dict) else None
    if not isinstance(account_id, str) or not ID.fullmatch(account_id):
        result["zone_read"] = "ACCOUNT_UNKNOWN"
        return result
    result["account_id"] = account_id

    ts, tunnels = get(opener, token,
        "/accounts/" + account_id + "/cfd_tunnel?" +
        urllib.parse.urlencode({"name": site, "is_deleted": "false", "per_page": 10}))
    result["tunnel_read"] = ts
    if ts == "READ_CONFIRMED":
        if not isinstance(tunnels, list):
            result["tunnel_read"] = "API_UNAVAILABLE"
        else:
            exact = [t for t in tunnels if isinstance(t, dict)
                     and t.get("name") == site
                     and t.get("account_tag") in (None, account_id)]
            result["tunnel_present"] = "YES" if len(exact) == 1 else "NO"

    ac, apps = get(opener, token,
        "/accounts/" + account_id + "/access/apps?per_page=100")
    result["access_read"] = ac
    if ac == "READ_CONFIRMED":
        if not isinstance(apps, list):
            result["access_read"] = "API_UNAVAILABLE"
        else:
            target = "*." + zone
            result["access_app_present"] = ("YES" if any(
                isinstance(a, dict) and a.get("domain") == target
                for a in apps) else "NO")
    result["overall"] = ("READ_CONFIRMED" if all(
        result[k] == "READ_CONFIRMED"
        for k in ("zone_read", "tunnel_read", "access_read")
    ) else "PARTIAL_READ")
    return result


def main():
    result = empty()
    try:
        payload = sys.stdin.read(160000)
        if len(payload) >= 160000:
            raise ValueError("oversized")
        values = json.loads(payload)
        if not isinstance(values, dict):
            raise ValueError("not object")
        result = audit(values.get("token"), values.get("site"))
    except (ValueError, UnicodeError, TypeError):
        result["zone_read"] = "INVALID_INPUT"
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
