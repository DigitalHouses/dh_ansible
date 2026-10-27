#!/usr/bin/env python3
"""Reconcile Filestash SMB only if absent or verifiably already correct.

Filestash intentionally encrypts the two middleware params in config.json.
Do not compare ciphertext to source JSON or replace unrecognized config:
the app's authenticated admin API exposes decrypted effective state.
All credentials remain in memory and are never logged.
"""
import argparse
import http.cookiejar
import json
import os
from pathlib import Path
import pwd
import re
import stat
import sys
import tempfile
import urllib.error
import urllib.request

PATH = Path("/opt/dh-filestash/data/state/config/config.json")
BASE = "http://127.0.0.1:8334"
SECRET_RE = re.compile(r"^[a-zA-Z0-9]{6,8}$")


class Blocked(RuntimeError):
    pass


def require(ok, error):
    if not ok:
        raise Blocked(error)


def desired():
    label = "DigitalHouses SMB"
    return {
        "connections": [{"label": label, "type": "samba"}],
        "middleware": {
            "identity_provider": {
                "type": "passthrough",
                "params": json.dumps({"strategy": "username_and_password"})},
            "attribute_mapping": {
                "related_backend": label,
                "params": json.dumps({
                    label: {"type": "samba", "host": "127.0.0.1",
                            "username": "{{ .user }}",
                            "password": "{{ .password }}"}})}
        }
    }


def passport(path):
    require(path.is_absolute() and not path.is_symlink()
            and path.parent.name == "credentials"
            and path.parent.parent.name == "digitalhouses"
            and path.parent.parent.parent.name.endswith("-pool"),
            "FILESTASH_CREDENTIAL_SCOPE_INVALID")
    st = path.lstat()
    require(stat.S_ISREG(st.st_mode) and st.st_uid == 0
            and stat.S_IMODE(st.st_mode) == 0o640 and st.st_nlink == 1,
            "FILESTASH_CREDENTIAL_UNSAFE")
    lines = path.read_text("ascii").splitlines()
    require(len(lines) == 2
            and lines[0] == "username=" + path.stem
            and lines[1].startswith("password=")
            and SECRET_RE.fullmatch(lines[1][9:]) is not None,
            "FILESTASH_CREDENTIAL_INVALID")
    return lines[1][9:]


def semantic_middleware(current, target):
    if not isinstance(current, dict) or set(current) != set(target):
        return False
    for component, static_key in (("identity_provider", "type"),
                                  ("attribute_mapping", "related_backend")):
        a = current.get(component)
        b = target.get(component)
        if (not isinstance(a, dict) or set(a) != set(b)
                or a.get(static_key) != b[static_key]
                or not isinstance(a.get("params"), str)):
            return False
        try:
            parsed = json.loads(a["params"])
            expected = json.loads(b["params"])
        except (TypeError, ValueError):
            return False
        if parsed != expected:
            return False
    return True


def semantic_config(current, target):
    return (isinstance(current, dict)
            and current.get("connections") == target["connections"]
            and semantic_middleware(current.get("middleware"),
                                    target["middleware"]))


def effective_admin_config(secret):
    """Fetch authoritative *decrypted* state from installed Filestash.

    Only localhost, fixed endpoints, and a cookie scoped to this opener.
    This method does not submit configuration changes.
    """
    cookies = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        urllib.request.HTTPCookieProcessor(cookies))
    headers = {"Content-Type": "application/json",
               "Accept": "application/json"}
    body = json.dumps({"password": secret}).encode("ascii")
    request = urllib.request.Request(
        BASE + "/admin/api/session", data=body, headers=headers, method="POST")
    stage = "ADMIN_LOGIN"
    try:
        with opener.open(request, timeout=12) as response:
            result = json.loads(response.read(16384))
        require(isinstance(result, dict) and result.get("status") == "ok",
                "FILESTASH_ADMIN_LOGIN_REJECTED")
        # Native Filestash returns an access_token for an exact JSON Accept
        # header. Its admin API explicitly accepts Authorization: Bearer.
        # The session cookie alone can be rejected by admin route middleware.
        session = result.get("result")
        token = session.get("access_token") if isinstance(session, dict) else None
        require(isinstance(token, str) and 16 <= len(token) <= 8192
                and "\r" not in token and "\n" not in token,
                "FILESTASH_ADMIN_ACCESS_TOKEN_UNAVAILABLE")
        stage = "ADMIN_CONFIG"
        config_request = urllib.request.Request(
            BASE + "/admin/api/config",
            headers={"Accept": "application/json",
                     "Authorization": "Bearer " + token},
            method="GET")
        with opener.open(config_request, timeout=12) as response:
            result = json.loads(response.read(1024 * 1024))
        require(isinstance(result, dict) and isinstance(result.get("result"), dict),
                "FILESTASH_RUNTIME_CONFIG_INVALID")
        return result["result"]
    except urllib.error.HTTPError as error:
        # HTTP status only, never URL, headers, cookies, or response body.
        code = error.code
        if type(code) is not int or not 400 <= code <= 599:
            raise Blocked("FILESTASH_" + stage + "_HTTP_ERROR") from None
        raise Blocked("FILESTASH_" + stage + "_HTTP_" + str(code)) from None
    except urllib.error.URLError:
        raise Blocked("FILESTASH_" + stage + "_NETWORK_ERROR") from None
    except TimeoutError:
        raise Blocked("FILESTASH_" + stage + "_TIMEOUT") from None


def reconcile(path=PATH, credential=None):
    require(not path.is_symlink() and not path.parent.is_symlink()
            and path.parent.is_dir(), "FILESTASH_STATE_PATH_UNSAFE")
    data = json.loads(path.read_text("utf-8")) if path.exists() else {}
    require(isinstance(data, dict), "FILESTASH_STATE_INVALID")
    target = desired()
    connections = data.get("connections")
    middleware = data.get("middleware")
    require(connections in (None, [], target["connections"]),
            "FILESTASH_EXISTING_CONNECTIONS_CONFLICT")
    if semantic_config(data, target):
        print("FILESTASH_SMB=UNCHANGED")
        return
    if middleware not in (None, {}):
        # Filestash encrypts both params at rest. The on-disk ciphertext
        # cannot be safely equated with our source JSON. Authenticate to the
        # running admin API and compare its DECRYPTED effective settings.
        require(credential is not None, "FILESTASH_RUNTIME_AUTH_REQUIRED")
        effective = effective_admin_config(passport(credential))
        require(semantic_config(effective, target),
                "FILESTASH_RUNTIME_SMB_CONFIG_DRIFT")
        print("FILESTASH_SMB=UNCHANGED")
        return
    require(connections in (None, [], target["connections"]),
            "FILESTASH_EXISTING_CONNECTIONS_CONFLICT")
    for key, value in target.items():
        data[key] = value
    output = (json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode()
    if path.exists() and path.read_bytes() == output:
        print("FILESTASH_SMB=UNCHANGED")
        return
    user = pwd.getpwnam("filestash")
    fd, stage = tempfile.mkstemp(prefix=".dh-smb-", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        os.fchown(fd, user.pw_uid, user.pw_gid)
        with os.fdopen(fd, "wb") as stream:
            stream.write(output)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(stage, path)
    finally:
        if os.path.exists(stage):
            os.unlink(stage)
    print("FILESTASH_SMB=RECONCILED")


if __name__ == "__main__":
    try:
        parser = argparse.ArgumentParser()
        parser.add_argument("--credential-file", type=Path, required=True)
        args = parser.parse_args()
        require(os.geteuid() == 0, "FILESTASH_ROOT_REQUIRED")
        reconcile(credential=args.credential_file)
    except (OSError, ValueError, RuntimeError, UnicodeError) as error:
        # Never expose encrypted config, passwords, cookies, or HTTP response.
        code = str(error) if isinstance(error, Blocked) else "RUNTIME"
        if not re.fullmatch(r"[A-Z0-9_]{3,80}", code):
            code = "RUNTIME"
        print("FILESTASH_SMB=BLOCKED_" + code, file=sys.stderr)
        sys.exit(2)
