#!/usr/bin/env python3
"""Native media password adapters. Root-only, fixed localhost APIs.

NO HTTP listener or sudo grant. A separate TLS-authenticated front-end is
required before enabling voluntary client password change.
"""
import importlib.machinery
import importlib.util
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import stat
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request

PROG_DIR = Path("/usr/local/sbin")


def helper(stem):
    path = PROG_DIR / stem
    source = importlib.machinery.SourceFileLoader(stem.replace("-", "_"), str(path))
    spec = importlib.util.spec_from_loader(source.name, source)
    module = importlib.util.module_from_spec(spec)
    source.exec_module(module)
    return module


def require(condition, reason):
    if not condition:
        raise RuntimeError(reason)


def silent(argv, *, input=None, timeout=20):
    # Never publish stdout, stderr, input, or environment from a secret operation.
    return subprocess.run(argv, input=input, stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL, timeout=timeout,
                          check=False).returncode == 0


class Samba:
    def __init__(self, username):
        self.username = username

    def verify(self, password):
        fd, name = tempfile.mkstemp(prefix=".dh-smb-auth-", dir="/run")
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="ascii") as file:
                file.write("username = " + self.username + "\n")
                file.write("password = " + password + "\n")
                file.flush()
                os.fsync(file.fileno())
            return silent(["/usr/bin/smbclient", "-A", name,
                           "//127.0.0.1/Media", "-c", "ls"], timeout=20)
        finally:
            os.unlink(name)

    def apply(self, current, new):
        require(self.verify(current), "SAMBA_OLD_AUTH_FAILED")
        require(silent(["/usr/bin/smbpasswd", "-s", "-a", self.username],
                       input=(new + "\n" + new + "\n").encode("ascii")),
                "SAMBA_PASSWORD_WRITE_FAILED")


class Jellyfin:
    def __init__(self, username):
        self.username = username
        self.native = helper("dh-jellyfin-bootstrap")

    def login(self, password):
        try:
            item = self.native.request(
                "POST", "/Users/AuthenticateByName",
                {"Username": self.username, "Pw": password})
            return item if isinstance(item, dict) and item.get("AccessToken") else None
        except (RuntimeError, urllib.error.URLError):
            return None

    def verify(self, password):
        return self.login(password) is not None

    def apply(self, current, new):
        auth = self.login(current)
        require(auth is not None, "JELLYFIN_OLD_LOGIN_FAILED")
        user = auth.get("User", {})
        user_id = user.get("Id")
        require(isinstance(user_id, str) and
                re.fullmatch(r"[a-fA-F0-9-]{32,36}", user_id),
                "JELLYFIN_USER_ID_UNVERIFIED")
        self.native.request(
            "POST", "/Users/" + user_id + "/Password",
            {"CurrentPw": current, "NewPw": new, "ResetPassword": False},
            token=auth["AccessToken"])


class Sambee:
    def __init__(self, username):
        self.username = username
        self.native = helper("dh-sambee-owner")

    def verify(self, password):
        return bool(self.native.token(self.username, password))

    def apply(self, current, new):
        token = self.native.token(self.username, current)
        require(token, "SAMBEE_OLD_LOGIN_FAILED")
        self.native.request("/api/auth/change-password",
                            {"current_password": current, "new_password": new},
                            bearer=token)


class Servarr:
    PORTS = {"radarr": 7878, "sonarr": 8989, "prowlarr": 9696}

    def __init__(self, app, username):
        require(app in self.PORTS, "ARR_UNKNOWN_SERVICE")
        self.app, self.username = app, username
        self.native = helper("dh-media-arr-auth")

    def verify(self, password):
        # The official Forms controller redirects failures to loginFailed=true,
        # while success sets a Forms authentication cookie and redirects home.
        target = "http://127.0.0.1:" + str(self.PORTS[self.app])
        body = urllib.parse.urlencode({
            "username": self.username, "password": password,
            "rememberMe": "off"}).encode("ascii")
        req = urllib.request.Request(target + "/login", data=body, headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": target, "Referer": target + "/login"})
        class StopRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, request, fp, code, msg, headers, url):
                return None
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), StopRedirect)
        try:
            with opener.open(req, timeout=12) as response:
                status, headers = response.status, response.headers
        except urllib.error.HTTPError as exc:
            if exc.code not in (301, 302, 303):
                return False
            status, headers = exc.code, exc.headers
        except urllib.error.URLError:
            return False
        location = headers.get("Location", "")
        return (status in (301, 302, 303)
                and "loginFailed" not in location
                and "Set-Cookie" in headers)

    def apply(self, current, new):
        require(self.verify(current), "ARR_OLD_LOGIN_FAILED_" + self.app)
        key = self.native.api_key(self.app)
        config = self.native.request(self.app, key)
        require(isinstance(config, dict)
                and isinstance(config.get("id"), int)
                and str(config.get("authenticationMethod", "")).lower() == "forms"
                and str(config.get("authenticationRequired", "")).lower() == "enabled"
                and config.get("username") == self.username,
                "ARR_AUTH_CONFIG_DRIFT_" + self.app)
        next_config = dict(config)
        next_config.update(password=new, passwordConfirmation=new)
        self.native.request(self.app, key, "PUT", next_config, config["id"])


class QBittorrent:
    def __init__(self, username, lan_ip):
        address = ipaddress.IPv4Address(lan_ip)
        require(not address.is_loopback and not address.is_multicast and
                not address.is_unspecified, "QBT_LAN_IP_INVALID")
        self.ip = str(address)
        self.username = username
        self.native = helper("dh-qbt-admin")

    def verify(self, password):
        # Connect to the guest's actual LAN interface, NOT loopback where
        # qBittorrent intentionally exempts local integration requests.
        origin = "http://" + self.ip + ":8080"
        body = urllib.parse.urlencode({"username": self.username,
                                       "password": password}).encode("ascii")
        req = urllib.request.Request(origin + "/api/v2/auth/login", data=body,
                                     headers={"Origin": origin,
                                              "Referer": origin + "/",
                                              "Content-Type":
                                                  "application/x-www-form-urlencoded"})
        try:
            with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=12) as response:
                return response.status == 200 and response.read(40).strip() == b"Ok."
        except (urllib.error.HTTPError, urllib.error.URLError):
            return False

    def apply(self, current, new):
        require(self.verify(current), "QBT_OLD_LAN_LOGIN_FAILED")
        prefs = self.native.call("/api/v2/app/preferences")
        require(prefs.get("web_ui_username") == self.username
                and prefs.get("bypass_local_auth") is True
                and prefs.get("bypass_auth_subnet_whitelist_enabled") is False,
                "QBT_WEBUI_AUTH_POLICY_DRIFT")
        self.native.call("/api/v2/app/setPreferences", {
            "web_ui_username": self.username,
            "web_ui_password": new,
            "bypass_local_auth": True,
            "bypass_auth_subnet_whitelist_enabled": False,
            "bypass_auth_subnet_whitelist": ""})


class Filestash:
    ENV = Path("/etc/digitalhouses/filestash.env")
    SERVICE = "filestash.service"

    @staticmethod
    def credential_hash():
        item = Filestash.ENV
        require(not item.is_symlink(), "FILESTASH_ENV_SYMLINK")
        st = item.stat()
        require(st.st_uid == 0 and stat.S_IMODE(st.st_mode) == 0o600
                and st.st_nlink == 1, "FILESTASH_ENV_UNSAFE")
        raw = item.read_text("ascii").strip()
        require(raw.startswith("ADMIN_PASSWORD=$2") and "\n" not in raw,
                "FILESTASH_HASH_MISSING")
        return raw.split("=", 1)[1]

    @staticmethod
    def service_hash():
        cmd = subprocess.run(
            ["systemctl", "show", "--property=MainPID", "--value",
             Filestash.SERVICE], capture_output=True, text=True, check=True)
        pid = int(cmd.stdout.strip())
        require(pid > 1, "FILESTASH_SERVICE_NOT_RUNNING")
        env = Path("/proc") / str(pid) / "environ"
        entries = env.read_bytes().split(b"\0")
        found = [v.decode("ascii")[15:] for v in entries
                 if v.startswith(b"ADMIN_PASSWORD=")]
        require(len(found) == 1, "FILESTASH_RUNNING_HASH_UNAVAILABLE")
        return found[0]

    def verify(self, password):
        try:
            val = self.credential_hash()
            if val != self.service_hash():
                return False
            fd, path = tempfile.mkstemp(prefix=".dh-bcrypt-", dir="/run")
            try:
                os.fchmod(fd, 0o600)
                with os.fdopen(fd, "w", encoding="ascii") as out:
                    out.write("admin:" + val + "\n")
                return silent(["/usr/bin/htpasswd", "-vi", path, "admin"],
                              input=(password + "\n").encode("ascii"))
            finally:
                os.unlink(path)
        except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError):
            return False

    def apply(self, current, new):
        require(self.verify(current), "FILESTASH_OLD_HASH_INVALID")
        result = subprocess.run(
            ["/usr/bin/htpasswd", "-niB", "-C", "12", ""],
            input=(new + "\n").encode("ascii"), capture_output=True,
            check=True)
        generated = result.stdout.decode("ascii").strip().split(":", 1)
        require(len(generated) == 2 and generated[1].startswith("$2"),
                "FILESTASH_HASH_GENERATION_FAILED")
        path = self.ENV
        fd, tmp = tempfile.mkstemp(prefix=".dh-filestash-", dir=path.parent)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="ascii") as file:
                file.write("ADMIN_PASSWORD=" + generated[1] + "\n")
                file.flush()
                os.fsync(file.fileno())
            os.replace(tmp, path)
        finally:
            if os.path.lexists(tmp):
                os.unlink(tmp)
        require(silent(["/usr/bin/systemctl", "restart", self.SERVICE],
                       timeout=40), "FILESTASH_RESTART_FAILED")
        # systemctl restart returns before the spawned web process is
        # necessarily ready. Immediate verification caused false failures,
        # followed by unnecessary multi-service rollback.
        import time
        deadline = time.monotonic() + 20
        while not self.verify(new):
            require(time.monotonic() < deadline,
                    "FILESTASH_NEW_HASH_NOT_READY")
            time.sleep(0.35)

