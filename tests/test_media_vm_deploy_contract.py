"""Offline contracts for one-click media VM deployment."""
import ast
from pathlib import Path
import importlib.util

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "bootstrap/dh-pve-media-deploy.py"
WORKER = ROOT / "bootstrap/pve-access-audit-request.sh"
PLAY = ROOT / "playbooks/plan-pve-media-vm.yml"


def module():
    spec = importlib.util.spec_from_file_location("dh_media_deploy", DEPLOY)
    obj = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obj)
    return obj


def test_python_and_declarative_task_syntax():
    ast.parse(DEPLOY.read_text())
    import yaml
    assert yaml.safe_load(PLAY.read_text())[0]["hosts"] == "site_runner"


def test_deploy_reuses_existing_worker_no_extra_template():
    import json
    worker = WORKER.read_text()
    play = PLAY.read_text()
    tasks = json.loads((ROOT / "catalog/semaphore-tasks.json").read_text())["tasks"]
    assert len([x for x in tasks if x["title"].startswith("80-Медиасервер")]) == 1
    assert "MEDIA_DEPLOY_(CREATE|RECREATE|KEEP)_(IMPORT|ERASE)" in worker
    assert "MEDIA_DEPLOY_{{ media_vm_action | upper }}_{{ media_raw_disk_action | upper }}" in play
    assert "dh-pve-access-audit.request" in play
    assert "dh-pve-media-deploy" in worker
    assert "--apply" in worker
    assert "MEDIA_PLAN" in worker


def test_image_download_and_host_compatibility():
    code = DEPLOY.read_text()
    for term in ("debian-13-generic-amd64.qcow2", "SHA512SUMS",
                 "hashlib.sha512", "qm(\"importdisk\"", "qm(\"start\"",
                 "local-lvm", "pct(\"ssh-keygen\"", "hostpci"):
        assert term in code
    assert 'old_guest_matches(vmid, site, data, managed=managed)' in code
    assert 'reconcile_media_vm_lifecycle(vmid, site, data, vm_action, disk_action)' in code
    assert '"VM_CREATE_REQUIRES_ABSENT_VM"' in code
    assert 'VM_" + vm_action.upper() + "_REQUIRES_EXISTING_VM' in code
    assert 'LEGACY_ZFS_GUID_MISMATCH' in code
    assert 'LEGACY_PHYSICAL_DISK_IDENTITY_MISMATCH' in code
    assert 'qm("destroy", vmid' in code
    for forbidden in ("qm stop 501", "qm stop 700",
                      "wipefs", "mkfs", "sgdisk", "dd if="):
        assert forbidden not in code


def test_metadata_validation_before_any_download(monkeypatch):
    m = module()
    monkeypatch.setattr(m.os, "geteuid", lambda: 0)
    monkeypatch.setattr(m.Path, "is_file", lambda *_: True)
    monkeypatch.setattr(m.Path, "exists", lambda *_: True)
    monkeypatch.setattr(m, "run", lambda *a, **k: ("pve" if a[0] == "/usr/bin/hostname" else "vmbr0 inet 198.51.100.30/24"))
    monkeypatch.setattr(m, "pct", lambda *args: (
        "ansible-demo-home" if args[0] == "hostname"
        else "site:\n  id: demo-home\n  owner_slug: demo\n  type: home" if args[-1].endswith("all.yml")
        else "media_vm: {}" if args[-1].endswith("media_vm.yml")
        else "pve:\n  host: 198.51.100.30\n  node: pve"
    ))
    data, _ = m.site_data("demo-home")
    assert data == {}

def test_media_vm_ownership_after_first_success_is_idempotent():
    import yaml
    text = PLAY.read_text()
    assert "difference([media_vm.vmid | int])" in text
    role = (ROOT / "roles/pve_media_vm_plan/tasks/main.yml").read_text()
    assert "rejectattr('vmid', 'equalto', media_vm.vmid | int)" in role
    assert "80-Медиасервер · Развёртывание" in (ROOT / "catalog/semaphore-tasks.json").read_text()


def test_first_boot_debian_apt_refresh_precedes_zfs_package_install():
    import yaml
    tasks = yaml.safe_load((ROOT / "roles/media_guest_setup/tasks/main.yml").read_text())
    names = [task["name"] for task in tasks]
    refresh = names.index("Refresh Debian APT indices after component reconciliation")
    install = names.index("Install QEMU Guest Agent, supported OpenZFS and Plex dependencies")
    components = names.index("Enable Debian contrib for supported ZFS packages")
    assert components < refresh < install
    assert tasks[refresh]["ansible.builtin.apt"]["update_cache"] is True
    assert tasks[refresh]["ansible.builtin.apt"]["cache_valid_time"] == 0
    assert tasks[install]["ansible.builtin.apt"]["update_cache"] is False
    assert tasks[refresh].get("when") is None




def test_media_external_apt_repos_are_always_refreshed_before_first_install():
    import yaml
    pairs = [
        (ROOT / "roles/media_guest_setup/tasks/main.yml",
         "Configure official Plex APT repository",
         "Refresh APT after adding or reconciling Plex repository",
         "Install Plex Media Server"),
        (ROOT / "roles/media_guest_setup/tasks/apps.yml",
         "Configure official Debian 13 Jellyfin package repository",
         "Refresh APT after adding or reconciling Jellyfin repository",
         "Install Jellyfin native media server"),
    ]
    for path, configured, refreshed, installed in pairs:
        tasks = yaml.safe_load(path.read_text())
        named = {task["name"]: idx for idx, task in enumerate(tasks)}
        assert named[configured] < named[refreshed] < named[installed]
        refresh = tasks[named[refreshed]]["ansible.builtin.apt"]
        install = tasks[named[installed]]["ansible.builtin.apt"]
        assert refresh["update_cache"] is True
        assert refresh["cache_valid_time"] == 0
        assert install["update_cache"] is False




def test_sambee_data_directory_writable_by_container_uid_before_launch():
    import yaml
    app_tasks = yaml.safe_load(
        (ROOT / "roles/media_guest_setup/tasks/apps.yml").read_text())
    names = [task["name"] for task in app_tasks]
    config = names.index("Prepare Sambee application configuration directory")
    data = names.index("Allow Sambee UID 1000 to persist its SQLite database")
    compose = names.index("Install Sambee container definition")
    start = names.index("Start or reconcile Sambee service")
    assert config < data < compose < start
    data_task = app_tasks[data]["ansible.builtin.file"]
    assert data_task["path"] == "/opt/dh-sambee/data"
    assert str(data_task["owner"]) == "1000"
    assert str(data_task["group"]) == "1000"
    assert data_task["recurse"] is True
    assert str(data_task["mode"]) == "0750"
    config_task = app_tasks[config]["ansible.builtin.file"]
    assert config_task["path"] == "/opt/dh-sambee"
    assert config_task["owner"] == "root"
    assert config_task["group"] == "root"
    compose_text = (
        ROOT / "roles/media_guest_setup/templates/media-sambee-compose.yml.j2"
    ).read_text()
    assert "./data:/app/data" in compose_text
    assert "ghcr.io/helgeklein/sambee:stable" in compose_text

def test_two_select_contract_has_no_legacy_cli_flags():
    deploy = DEPLOY.read_text()
    worker = WORKER.read_text()
    guest_tasks = (ROOT / "roles/media_guest_setup/tasks/main.yml").read_text()
    disk_source = (ROOT / "roles/media_guest_setup/files/dh-media-storage.py").read_text()
    assert 'parser.add_argument("--vm-action", choices=("create", "recreate", "keep"), required=True)' in deploy
    assert 'parser.add_argument("--raw-disk-action", choices=("import", "erase"), required=True)' in deploy
    assert 'parser.add_argument("--action", choices=("import", "erase"), required=True)' in disk_source
    for stale in ('--storage-action', '--vm-policy', '--disk-policy',
                  'media_storage_action', 'media_vm_policy', 'media_disk_policy'):
        assert stale not in deploy
        assert stale not in worker
        assert stale not in guest_tasks
    assert 'media_raw_disk_action' in guest_tasks
    assert 'ensure_create(data, disk, physical, "erase")' in disk_source



def test_native_filestash_state_follows_binary_directory_and_is_writable():
    """Guard against /usr/local/bin/data/state/log crash on a fresh Debian VM."""
    import yaml
    tasks = yaml.safe_load(
        (ROOT / "roles/media_guest_setup/tasks/apps.yml").read_text())
    by_name = {task["name"]: (index, task) for index, task in enumerate(tasks)}
    root_i, root_task = by_name["Create root-owned Filestash executable directory"]
    state_i, state_task = by_name["Create persistent native Filestash state directories"]
    bin_i, bin_task = by_name["Download upstream native Filestash executable"]
    unit_i, unit_task = by_name["Install Filestash service definition"]
    start_i, start_task = by_name["Start Filestash"]
    check_i, _ = by_name["Wait for Filestash HTTP UI"]
    assert root_i < state_i < bin_i < unit_i < start_i < check_i
    root_args = root_task["ansible.builtin.file"]
    assert (root_args["path"], root_args["owner"], root_args["group"]) == (
        "/opt/dh-filestash", "root", "root")
    state_args = state_task["ansible.builtin.file"]
    assert state_args["owner"] == "filestash"
    assert state_args["group"] == "filestash"
    assert "recurse" not in state_args
    required = {"log", "config", "db", "certs", "plugins", "search"}
    paths = state_task["loop"]
    assert {f"/opt/dh-filestash/data/state/{name}" for name in required}.issubset(paths)
    binary = bin_task["ansible.builtin.get_url"]
    assert binary["dest"] == "/opt/dh-filestash/filestash"
    assert binary["owner"] == "root" and binary["force"] is False
    unit = (ROOT / "roles/media_guest_setup/templates/media-filestash.service.j2").read_text()
    assert "User=filestash" in unit
    assert "ExecStart=/opt/dh-filestash/filestash" in unit
    assert "WorkingDirectory=/opt/dh-filestash" in unit
    assert "/usr/local/bin/filestash" not in unit
    assert "restarted" in start_task["ansible.builtin.systemd_service"]["state"]


def test_media_deploy_cannot_ignore_read_only_plan_blockers():
    import yaml
    source = yaml.safe_load((ROOT / "playbooks/plan-pve-media-vm.yml").read_text())
    assert len(source) == 1
    steps = source[0]["tasks"]
    gate = next(step for step in steps if step.get("name") ==
                "Provision only when canonical Fleet authorizes media server")
    submit = next(index for index, step in enumerate(steps)
                  if step.get("name") ==
                  "Submit fixed-purpose media deploy to existing PVE root worker")
    gate_index = steps.index(gate)
    assert gate_index < submit, "The blocker gate must precede the root-worker request"
    conditions = gate["ansible.builtin.assert"]["that"]
    assert "media_plan_summary.blockers | length == 0" in conditions
    assert "media_plan_summary.blockers is not string" in conditions
    assert "MEDIA_DEPLOY_BLOCKED_BY_PREFLIGHT" in gate["ansible.builtin.assert"]["fail_msg"]
    # Our existing Fleet explicitly has not verified DHCP exclusion. This
    # prevents deployment even if hardware and PVE ownership are healthy.
    safety = (ROOT / "roles/pve_media_vm_plan/tasks/main.yml").read_text()
    assert "STATIC_IP_NOT_VERIFIED" in safety
