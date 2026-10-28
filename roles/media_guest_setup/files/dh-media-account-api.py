#!/usr/bin/env python3
"""Root-only local credential rotation API over a Unix socket.

No TCP listener. Nginx HTTPS is the only supported ingress. Requires old owner
password, CSRF challenge, strict Origin and LAN source, bounded request, and
rate limit. Never logs passwords or stores the proposed password on this server.
"""
import collections
import hmac
import http.server
import ipaddress
import json
import os
from pathlib import Path
import secrets
import socket
import socketserver
import stat
import subprocess
import sys
import threading
import time
from http.cookies import SimpleCookie

SOCKET = Path("/run/dh-media-account/service.sock")
ORIGIN = "https://192.168.11.238"
LAN = ipaddress.ip_network("192.168.11.0/24")
ROTATOR = "/usr/local/sbin/dh-media-password-rotate"
SESSIONS = {}
ATTEMPTS = collections.defaultdict(list)
LOCK = threading.RLock()


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args):
        # Don't put client IPs, request URLs, body or passwords in journal.
        pass

    def result(self, status, data, cookie=None):
        raw = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(raw)

    def source(self):
        if self.headers.get("X-Forwarded-Proto") != "https":
            return None
        if self.headers.get("Host") != "192.168.11.238":
            return None
        raw = self.headers.get("X-Real-IP", "")
        try:
            ip = ipaddress.ip_address(raw)
            if ip in LAN:
                return str(ip)
        except ValueError:
            pass
        return None

    def do_GET(self):
        if self.path != "/account/api/session" or self.source() is None:
            self.result(404, {"ok": False})
            return
        session_id = secrets.token_urlsafe(32)
        csrf = secrets.token_urlsafe(32)
        now = time.monotonic()
        with LOCK:
            for key, value in list(SESSIONS.items()):
                if value[2] < now:
                    del SESSIONS[key]
            SESSIONS[session_id] = (csrf, self.source(), now + 600)
        self.result(200, {"csrf": csrf, "policy": "6-8 ASCII letters or digits"},
                    "__Host-DHMedia=" + session_id +
                    "; Secure; HttpOnly; Path=/; SameSite=Strict; Max-Age=600")

    def do_POST(self):
        ip = self.source()
        if self.path != "/account/api/change" or ip is None:
            self.result(404, {"ok": False})
            return
        if self.headers.get("Origin") != ORIGIN:
            self.result(403, {"ok": False, "code": "ORIGIN"})
            return
        if not self.headers.get("Content-Type", "").startswith("application/json"):
            self.result(415, {"ok": False, "code": "FORMAT"})
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            size = 0
        if size <= 0 or size > 512:
            self.result(413, {"ok": False, "code": "SIZE"})
            return
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
        except Exception:
            self.result(403, {"ok": False, "code": "SESSION"})
            return
        session_id = cookie.get("__Host-DHMedia")
        csrf_header = self.headers.get("X-CSRF", "")
        now = time.monotonic()
        with LOCK:
            record = SESSIONS.pop(session_id.value, None) if session_id else None
            ATTEMPTS[ip] = [t for t in ATTEMPTS[ip] if now - t < 900]
            limited = len(ATTEMPTS[ip]) >= 5
            ATTEMPTS[ip].append(now)
        if limited:
            self.result(429, {"ok": False, "code": "RATE_LIMIT"})
            return
        if (not record or record[2] < now or record[1] != ip
                or not hmac.compare_digest(record[0], csrf_header)):
            self.result(403, {"ok": False, "code": "SESSION"})
            return
        try:
            request = json.loads(self.rfile.read(size).decode("utf-8"))
        except (ValueError, UnicodeError):
            self.result(400, {"ok": False, "code": "FORMAT"})
            return
        if not isinstance(request, dict) or set(request) != {"old", "new"}:
            self.result(400, {"ok": False, "code": "FORMAT"})
            return
        old, new = request["old"], request["new"]
        if (not isinstance(old, str) or not isinstance(new, str) or
                not (6 <= len(old) <= 8) or not (6 <= len(new) <= 8) or
                not old.isascii() or not new.isascii() or
                not old.isalnum() or not new.isalnum() or old == new):
            self.result(400, {"ok": False, "code": "POLICY"})
            return
        # Serialize actual rotations; existing transaction engine also holds a lock.
        with ROTATION_LOCK:
            try:
                completed = subprocess.run(
                    [ROTATOR],
                    input=json.dumps({"old": old, "new": new}) + "\n",
                    capture_output=True, text=True, timeout=150, check=False,
                    env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C.UTF-8"})
            except (OSError, subprocess.TimeoutExpired):
                self.result(503, {"ok": False, "code": "UNAVAILABLE"})
                return
        if completed.returncode == 0 and completed.stdout.strip() == "MEDIA_OWNER_ROTATION=COMMITTED":
            self.result(200, {"ok": True, "code": "COMMITTED"})
        else:
            # Do not echo internal errors, usernames, or service identities.
            self.result(409, {"ok": False, "code": "NOT_COMPLETED"})


ROTATION_LOCK = threading.Lock()


class UnixServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 8


def main():
    if os.geteuid() != 0:
        raise SystemExit("ROOT_REQUIRED")
    if not Path(ROTATOR).is_file():
        raise SystemExit("ROTATION_ENGINE_MISSING")
    SOCKET.parent.mkdir(parents=True, exist_ok=True)
    if SOCKET.exists() or SOCKET.is_symlink():
        mode = SOCKET.lstat().st_mode
        if not stat.S_ISSOCK(mode) or SOCKET.is_symlink():
            raise SystemExit("SOCKET_PATH_UNSAFE")
        SOCKET.unlink()
    with UnixServer(str(SOCKET), Handler) as server:
        import grp
        os.chown(SOCKET, 0, grp.getgrnam("www-data").gr_gid)
        os.chmod(SOCKET, 0o660)
        server.serve_forever(poll_interval=0.3)


if __name__ == "__main__":
    main()
