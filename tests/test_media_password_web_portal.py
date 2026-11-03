"""Media password web UI security and PVE CLI persistence contracts."""
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def read(name):
    return (ROOT / name).read_text()


def test_native_web_operation_exists_without_ansible_at_runtime():
    tasks = yaml.safe_load(read("roles/media_guest_setup/tasks/password_portal.yml"))
    assert "dh-media-account-api.service" in str(tasks)
    assert "dh-media-account-api.py" in str(tasks)
    assert "client_ca_trust_required" in str(tasks)
    assert "ansible_runtime_dependency" in str(tasks)
    assert "password_portal.yml" in read("roles/media_guest_setup/tasks/main.yml")


def test_only_https_access_and_local_unix_socket():
    api = read("roles/media_guest_setup/files/dh-media-account-api.py")
    nginx = read("roles/media_guest_setup/templates/media-account-nginx.conf.j2")
    base = read("roles/media_guest_setup/templates/media-portal-nginx.conf.j2")
    assert 'SOCKET = Path("/run/dh-media-account/service.sock")' in api
    assert "UnixStreamServer" in api
    assert "listen 443 ssl" in nginx
    assert "proxy_pass http://unix:" in nginx
    assert "access_log off" in nginx
    assert "X-Forwarded-Proto https" in nginx
    assert "return 308 https://$host$request_uri" in base
    assert "proxy_pass" not in base


def test_root_only_portal_does_not_export_current_password():
    api = read("roles/media_guest_setup/files/dh-media-account-api.py")
    assert 'if os.geteuid() != 0:' in api
    assert '"/account/api/change"' in api
    assert '"/account/api/session"' in api
    assert 'self.headers.get("Origin") != ORIGIN' in api
    assert "X-CSRF" in api
    assert "Rate" not in api or "ATTEMPTS" in api
    assert "ATTEMPTS[ip]" in api
    assert "log_message" in api
    assert 'input=json.dumps({"old": old, "new": new})' in api
    assert "password=" not in api
    assert "NoNewPrivileges=yes" in read("roles/media_guest_setup/files/dh-media-account-api.service")


def test_password_form_is_on_portal_and_never_sends_password_over_http():
    landing = read("roles/media_guest_setup/templates/media-portal-index.html.j2")
    page = read("roles/media_guest_setup/files/dh-media-account.html")
    assert "Управление доступом" in landing
    assert 'href="https://{{ media_ip }}/account/"' in landing
    assert 'location.protocol!=="https:"' in page
    assert 'fetch("/account/api/change"' in page
    assert 'autocomplete="current-password"' in page


def test_protected_host_bootstrap_installs_pve_cli_and_guest_assets():
    bootstrap = read("bootstrap/semaphore-host-install.py")
    assert 'bootstrap/dh-pve-media-password-show.py' in bootstrap
    for part in ["tasks/password_portal.yml", "files/dh-media-account-api.py",
                 "files/dh-media-account-api.service", "files/dh-media-account.html",
                 "templates/media-account-nginx.conf.j2"]:
        assert part in bootstrap
