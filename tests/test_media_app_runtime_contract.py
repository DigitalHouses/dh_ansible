"""Offline media v3 app-credential and runtime security contracts."""
import ast
import base64
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


def module(relative, name):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    item = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(item)
    return item


def test_app_passwords_created_once_and_reused():
    accounts = module("roles/media_guest_setup/files/dh-media-app-accounts.py", "app_accounts")
    with tempfile.TemporaryDirectory() as scratch:
        folder = Path(scratch) / "apps"
        folder.mkdir()
        password, created = accounts.account(folder, "jellyfin")
        assert created and len(password) == 24
        second, created = accounts.account(folder, "jellyfin")
        assert not created and password == second
        assert (folder / "jellyfin.txt").stat().st_mode & 0o777 == 0o600
        assert "username=serzhan_admin" in (folder / "jellyfin.txt").read_text()


def test_qbittorrent_sets_password_only_once_and_requires_local_only_api():
    item = module("roles/media_guest_setup/files/dh-qbt-admin.py", "qbt")
    with tempfile.TemporaryDirectory() as scratch:
        root = Path(scratch) / "digitalhouses" / "credentials" / "apps"
        root.mkdir(parents=True)
        secret = root / "qbittorrent.txt"
        secret.write_text("username=serzhan_admin\npassword=" + "B" * 24 + "\n")
        secret.chmod(0o600)
        state = {"bypass_local_auth": True, "bypass_auth_subnet_whitelist_enabled": False,
                 "web_ui_username": "admin"}
        changes = []

        def local_api(path, data=None):
            if data is not None:
                changes.append(data)
                state.update(data)
                return {}
            return dict(state)

        with mock.patch.object(item, "call", side_effect=local_api), \
             mock.patch.object(item.os, "geteuid", return_value=0):
            item.reconcile(secret)
            item.reconcile(secret)
        assert len(changes) == 1
        assert changes[0]["web_ui_username"] == "serzhan_admin"
        assert changes[0]["web_ui_password"] == "B" * 24
        assert changes[0]["bypass_auth_subnet_whitelist_enabled"] is False


def test_encrypted_fleet_mqtt_runtime_worker_roundtrip():
    src = (ROOT / "bootstrap/dh-pve-media-mqtt-runtime-commission.py").read_text()
    tree = ast.parse(src)
    worker = next(n.value.value for n in tree.body
                  if isinstance(n, ast.Assign) and any(
                      isinstance(t, ast.Name) and t.id == "WORKER" for t in n.targets))
    from ansible.parsing.vault import VaultLib, VaultSecret
    import yaml
    master = "SYNTHETIC_MASTER_DO_NOT_USE"
    vault = VaultLib([("default", VaultSecret(master.encode()))])
    plain = yaml.safe_dump({"fleet_secrets": {
        "sites": {"serzhan-home": {"plex_agent": {"mqtt": {
            "username": "synthetic", "password": "sampleonly"}}}},
        "shared": {"unrelated": "preserved"}}}).encode()
    encrypted = vault.encrypt(plain, secret=VaultSecret(master.encode()))
    payload = json.dumps({"master": master,
                          "cipher": base64.b64encode(encrypted).decode()})
    cmd = ["/opt/ansible/venv/bin/python", "-c", worker]
    proc = subprocess.run(cmd, input=payload, capture_output=True,
                          text=True, timeout=10)
    assert proc.returncode == 0
    assert json.loads(proc.stdout) == {
        "username": "synthetic", "password": "sampleonly"}
    assert proc.stderr == ""


def test_media_runner_preflights_mqtt_before_any_vm_apply():
    script = (ROOT / "bootstrap/pve-access-audit-request.sh").read_text()
    assert script.index("dh-pve-media-runtime") < script.index(
        'sudo -n /usr/local/sbin/dh-pve-media-deploy')
    assert '> "$MEDIA_RUNTIME_FILE"' in script
    assert '"dh_media_runtime_secret_file=$MEDIA_RUNTIME_FILE"' in script
    assert 'rm -f -- "$MEDIA_RUNTIME_FILE"' in script


def test_guest_never_requires_mqtt_secret_in_plain_inventory():
    preflight = (ROOT / "playbooks/media-accounts-preflight.yml").read_text()
    assert "media_vm.plex_agent.mqtt.password is not defined" in preflight
    assert "media_vm.plex_agent.mqtt.username is not defined" in preflight
    guest = (ROOT / "playbooks/setup-pve-media-guest.yml").read_text()
    assert "dh_media_runtime.runtime_secrets.site_id == site.id" in guest
    assert "no_log: true" in guest


def test_all_media_assets_in_pinned_bootstrap_download_manifest():
    text = (ROOT / "bootstrap/semaphore-host-install.py").read_text()
    tree = ast.parse(text)
    src = next(node.value for node in tree.body if isinstance(node, ast.Assign)
               and any(isinstance(t, ast.Name) and t.id == "CODE_FILES"
                       for t in node.targets))
    names = set(ast.literal_eval(src))
    assert "bootstrap/dh-pve-media-runtime.py" in names
    for item in ("roles/media_guest_setup/tasks/app_accounts.yml",
                 "roles/media_guest_setup/files/dh-media-app-accounts.py",
                 "roles/media_guest_setup/files/dh-qbt-admin.py",
                 "roles/media_guest_setup/tasks/plex_agent.yml",
                 "roles/media_guest_setup/tasks/jellyfin_bootstrap.yml",
                 "playbooks/setup-pve-media-guest.yml"):
        assert item in names and (ROOT / item).is_file()
