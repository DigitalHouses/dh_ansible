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
import secrets
import stat
import string
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request

PATH = Path("/opt/dh-filestash/data/state/config/config.json")
BASE = "http://127.0.0.1:8334"
BACKUP_ROOT = Path("/var/backups/digitalhouses/filestash")
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


def native_admin_middleware(form):
    """Project Filestash's native admin form descriptors to current values.

    GET /admin/api/config returns a form schema, not config.json: each leaf
    is a descriptor containing a `value` field. It does not expose the
    "connections" list, which has already been checked on disk.
    """
    require(isinstance(form, dict) and isinstance(form.get("middleware"), dict),
            "FILESTASH_RUNTIME_CONFIG_INVALID")
    source = form["middleware"]
    expected = desired()["middleware"]
    require(set(source) == set(expected), "FILESTASH_RUNTIME_SMB_CONFIG_DRIFT")
    normalized = {}
    for component, fields in expected.items():
        group = source.get(component)
        require(isinstance(group, dict) and set(group) == set(fields),
                "FILESTASH_RUNTIME_SMB_CONFIG_DRIFT")
        normalized[component] = {}
        for field in fields:
            descriptor = group.get(field)
            require(isinstance(descriptor, dict)
                    and isinstance(descriptor.get("value"), str),
                    "FILESTASH_RUNTIME_CONFIG_INVALID")
            normalized[component][field] = descriptor["value"]
    return normalized


def effective_admin_config(secret):
    """Fetch authoritative *decrypted* state from installed Filestash.

    Only localhost, fixed endpoints, and a cookie scoped to this opener.
    This method does not submit configuration changes.
    """
    cookies = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        urllib.request.HTTPCookieProcessor(cookies))
    # Filestash SecureOrigin requires this exact header for browser-style
    # authenticated calls. Keep API protection enabled; do not bypass WAF.
    headers = {"Content-Type": "application/json",
               "Accept": "application/json",
               "X-Requested-With": "XmlHttpRequest"}
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
                     "X-Requested-With": "XmlHttpRequest",
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



class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def broken_idp_entrypoint():
    """Observe only the exact local failure; never login as a web user."""
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}), _NoRedirect())
    url = BASE + "/api/session/auth/?action=redirect&label=DigitalHouses%20SMB"
    try:
        with opener.open(url, timeout=5):
            return False
    except urllib.error.HTTPError as error:
        if error.code != 307:
            return False
        location = urllib.parse.urlsplit(error.headers.get("Location", ""))
        query = urllib.parse.parse_qs(location.query)
        return (location.path == "/" and
                query.get("error") == ["Not Valid"] and
                query.get("trace", [""])[0].startswith(
                    "unpacking idp - invalid character"))
    except (OSError, TimeoutError, urllib.error.URLError):
        return False


def _write_config(path, data):
    output = (json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode()
    if path.exists() and path.read_bytes() == output:
        return False
    user = pwd.getpwnam("filestash")
    fd, stage = tempfile.mkstemp(prefix=".dh-smb-", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        meta = os.fstat(fd)
        if (meta.st_uid, meta.st_gid) != (user.pw_uid, user.pw_gid):
            os.fchown(fd, user.pw_uid, user.pw_gid)
        with os.fdopen(fd, "wb") as stream:
            stream.write(output)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(stage, path)
    finally:
        if os.path.exists(stage):
            os.unlink(stage)
    return True


def _repair_broken_idp(path, data, target):
    """Recover only our exact broken passthrough; preserve a root-only backup."""
    require(data.get("connections") == target["connections"],
            "FILESTASH_RECOVERY_FOREIGN_CONNECTION")
    require(broken_idp_entrypoint(), "FILESTASH_RECOVERY_NO_PROVEN_IDP_FAILURE")
    general = data.get("general", {})
    require(isinstance(general, dict) and
            re.fullmatch(r"[a-zA-Z0-9]{16}", general.get("secret_key", "")),
            "FILESTASH_RECOVERY_SECRET_KEY_UNKNOWN")
    middleware = data.get("middleware", {})
    require(isinstance(middleware, dict) and
            set(middleware) == set(target["middleware"]),
            "FILESTASH_RECOVERY_FOREIGN_MIDDLEWARE")
    for name, expected in target["middleware"].items():
        current = middleware.get(name)
        require(isinstance(current, dict) and
                set(current) == set(expected),
                "FILESTASH_RECOVERY_FOREIGN_MIDDLEWARE")
        static_key = ("type" if name == "identity_provider"
                      else "related_backend")
        require(current.get(static_key) == expected[static_key],
                "FILESTASH_RECOVERY_FOREIGN_MIDDLEWARE")
        opaque = current.get("params")
        require(isinstance(opaque, str) and len(opaque) >= 32 and
                not opaque.lstrip().startswith(("{", "[")),
                "FILESTASH_RECOVERY_UNEXPECTED_PARAMS")
    meta = path.lstat()
    user = pwd.getpwnam("filestash")
    require(stat.S_ISREG(meta.st_mode) and meta.st_nlink == 1 and
            meta.st_uid == user.pw_uid,
            "FILESTASH_RECOVERY_FILE_UNSAFE")
    original = path.read_bytes()
    # The original opaque state is never overwritten without a retained copy.
    # Keep backups outside the service-writable state directory.
    BACKUP_ROOT.mkdir(parents=True, mode=0o700, exist_ok=True)
    backup_meta = BACKUP_ROOT.lstat()
    require(stat.S_ISDIR(backup_meta.st_mode) and
            backup_meta.st_uid == os.geteuid() and
            stat.S_IMODE(backup_meta.st_mode) == 0o700 and
            not BACKUP_ROOT.is_symlink(),
            "FILESTASH_RECOVERY_BACKUP_DIR_UNSAFE")
    fd, backup = tempfile.mkstemp(prefix=".dh-filestash-idp-backup-",
                                  suffix=".json", dir=BACKUP_ROOT)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(original)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        os.unlink(backup)
        raise
    for name, expected in target["middleware"].items():
        data["middleware"][name]["params"] = expected["params"]
    require(_write_config(path, data), "FILESTASH_RECOVERY_NO_CHANGE")
    # Filestash must restart to load plaintext and encrypt with the
    # already-persisted general.secret_key; Ansible handles the restart.
    print("FILESTASH_SMB=RECONCILED")

def reconcile(path=PATH, credential=None, repair_broken_idp=False):
    require(not path.is_symlink() and not path.parent.is_symlink()
            and path.parent.is_dir(), "FILESTASH_STATE_PATH_UNSAFE")
    data = json.loads(path.read_text("utf-8")) if path.exists() else {}
    require(isinstance(data, dict), "FILESTASH_STATE_INVALID")
    target = desired()
    connections = data.get("connections")
    middleware = data.get("middleware")
    require(connections in (None, [], target["connections"]),
            "FILESTASH_EXISTING_CONNECTIONS_CONFLICT")
    general = data.get("general", {})
    require(isinstance(general, dict), "FILESTASH_GENERAL_STATE_INVALID")
    secret_seeded = False
    # Pin the key BEFORE first boot. Filestash can otherwise encrypt the
    # middleware using its pre-initialisation (empty) key.
    if not general.get("secret_key"):
        require(middleware in (None, {}) or
                semantic_middleware(middleware, target["middleware"]),
                "FILESTASH_SECRET_MISSING_WITH_UNKNOWN_MIDDLEWARE")
        general["secret_key"] = "".join(
            secrets.choice(string.ascii_letters + string.digits)
            for _ in range(16))
        data["general"] = general
        secret_seeded = True
    if semantic_config(data, target) and not secret_seeded:
        print("FILESTASH_SMB=UNCHANGED")
        return
    if middleware not in (None, {}) and not secret_seeded:
        if repair_broken_idp:
            # Fail closed unless this precise instance and precise runtime
            # symptom match the known first-boot encryption defect.
            try:
                _repair_broken_idp(path, data, target)
                return
            except Blocked as error:
                if str(error) != "FILESTASH_RECOVERY_NO_PROVEN_IDP_FAILURE":
                    raise
        # Filestash encrypts both params at rest. The on-disk ciphertext
        # cannot be safely equated with our source JSON. Authenticate to the
        # running admin API and compare its DECRYPTED effective settings.
        require(credential is not None, "FILESTASH_RUNTIME_AUTH_REQUIRED")
        admin_form = effective_admin_config(passport(credential))
        effective = {"connections": connections,
                     "middleware": native_admin_middleware(admin_form)}
        require(semantic_config(effective, target),
                "FILESTASH_RUNTIME_SMB_CONFIG_DRIFT")
        print("FILESTASH_SMB=UNCHANGED")
        return
    require(connections in (None, [], target["connections"]),
            "FILESTASH_EXISTING_CONNECTIONS_CONFLICT")
    for key, value in target.items():
        data[key] = value
    if not _write_config(path, data):
        print("FILESTASH_SMB=UNCHANGED")
        return
    print("FILESTASH_SMB=RECONCILED")


if __name__ == "__main__":
    try:
        parser = argparse.ArgumentParser()
        parser.add_argument("--credential-file", type=Path, required=True)
        parser.add_argument("--repair-broken-idp", action="store_true")
        args = parser.parse_args()
        require(os.geteuid() == 0, "FILESTASH_ROOT_REQUIRED")
        reconcile(credential=args.credential_file,
                  repair_broken_idp=args.repair_broken_idp)
    except (OSError, ValueError, RuntimeError, UnicodeError) as error:
        # Never expose encrypted config, passwords, cookies, or HTTP response.
        code = str(error) if isinstance(error, Blocked) else "RUNTIME"
        if not re.fullmatch(r"[A-Z0-9_]{3,80}", code):
            code = "RUNTIME"
        print("FILESTASH_SMB=BLOCKED_" + code, file=sys.stderr)
        sys.exit(2)
