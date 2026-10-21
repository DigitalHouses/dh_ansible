"""Semaphone-only PVE host installer static and unit gates."""
from pathlib import Path
import importlib.util
import pytest

SRC=Path(__file__).resolve().parents[1] / "bootstrap/semaphore-host-install.py"

def load():
    spec=importlib.util.spec_from_file_location("dh_semaphore_host", SRC)
    m=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m

def test_sem_runner_host_has_exact_one_guest_and_no_wizard():
    s=SRC.read_text()
    assert "CTID = 1231" in s
    assert "pct\", \"destroy" not in s
    assert "pct\", \"create" in s
    assert "runner-fresh-install.py" not in s
    assert "setup-wizard" not in s or "Legacy Web Wizard detected" not in s
    assert "SITE_ESCROW_GATE=PASS" in s
    assert "verify_site_escrow(checkout, args.site)" in s
    assert "semaphore-guest-install.py" in s and "semaphore-project.py" in s

def test_git_oauth_fleet_gate_precedes_guest_create():
    s=SRC.read_text()
    body=s.split("def apply(args, template):",1)[1].split("\ndef main(",1)[0]
    assert body.index("authenticate_git(") < body.index("validate_fleet_password(")
    assert body.index("validate_fleet_password(") < body.index("create_runner(")
    assert "GH_CONFIG_DIR" in s
    assert "--expected-commit" in s
    assert "--expected-fleet-commit" in s
    assert 'CODE_REPO = "DigitalHouses/dh_ansible"' in s
    assert 'FLEET_REPO = "DigitalHouses/dh_ansible_fleet_detail"' in s
    assert '"--web"' in s
    assert "def register_deploy_key(" not in s
    assert "install_site_inventory(" in s

def test_template_selects_latest_and_rejects_missing():
    m=load()
    text=("local:vztmpl/debian-13-standard_13.1-2_amd64.tar.zst\n"
          "local:vztmpl/debian-13-standard_13.6-1_amd64.tar.zst")
    assert m.select_template(text).endswith("13.6-1_amd64.tar.zst")
    with pytest.raises(m.Blocked):
        m.select_template("no Debian 13")

def test_script_never_pushes_master_or_git_personal_access_token_to_guest():
    s=SRC.read_text()
    assert "gh auth token" not in s
    assert 'run("pct", "push", str(CTID), str(source), staging' in s
    assert '"/root/dh-fleet-deploy-key", timeout=30' not in s
    assert "Fleet master password" in s


def test_guest_acceptance_information_is_printed_to_operator():
    src=SRC.read_text()
    assert 'print(pct_guest(*guest_args, timeout=1000), flush=True)' in src
    assert 'TLS_CERT_SHA256=' in (Path(__file__).resolve().parents[1]/'bootstrap/semaphore-guest-install.py').read_text()

def test_fresh_create_requires_no_magic_confirmation_but_keeps_strict_identity_gate():
    s=SRC.read_text()
    m=load()
    assert "creation_confirmed" not in s
    assert "Type CREATE" not in s
    assert 'CT_CREATE_CONFIRMATION=SITE_ID_ALREADY_CONFIRMED' in s
    assert 'host_preflight(args.site, args.runner_cidr, args.gateway, args.pve_ip,' in s
    assert 'CT1231 / VM1231 already exists; no overwrite' in s
    assert m.runner_hostname("demo-office") == "ansible-demo-office"


def test_guest_failure_output_is_allowlisted_no_password_or_raw_traceback():
    m=load()
    argv=("pct", "exec", "1231", "--", "python3",
          "/opt/digitalhouses/bootstrap/semaphore-guest-install.py", "--apply")
    raw=("SEMAPHORE_GUEST_PREFLIGHT=PASS\nOS_DEPENDENCIES=START\n"
         "PASSWORD=super-secret-test\n"
         "INSTALL=BLOCKED: Command failed: apt-get, rc=100\n")
    lines=m.safe_guest_failure_lines(argv, raw, "Traceback: private-token-test")
    assert lines==["SEMAPHORE_GUEST_PREFLIGHT=PASS", "OS_DEPENDENCIES=START",
                   "INSTALL=BLOCKED: Command failed: apt-get, rc=100"]
    assert m.safe_guest_failure_lines(("pct","config","1231"),raw,"")==[]
    assert m.safe_guest_failure_lines(("gh","auth","status"),raw,"")==[]


def test_guest_failure_is_visible_only_when_safe(capsys,monkeypatch):
    from types import SimpleNamespace
    m=load()
    def failed(*argv,**kwargs):
        return SimpleNamespace(returncode=2,stdout="PRIVATE TOKEN MUST NOT PRINT",
            stderr="INSTALL=BLOCKED: Command failed: pip, rc=1")
    monkeypatch.setattr(m.subprocess,"run",failed)
    with pytest.raises(m.Blocked,match="pct, rc=2"):
        m.run("pct","exec","1231","--","python3",
              "/opt/digitalhouses/bootstrap/semaphore-guest-install.py","--apply")
    out=capsys.readouterr().out
    assert "GUEST_DIAGNOSTIC: INSTALL=BLOCKED: Command failed: pip, rc=1" in out
    assert "PRIVATE TOKEN" not in out

def test_resume_requires_explicit_option_and_separate_confirmation():
    s=SRC.read_text()
    assert 'resume_group.add_argument("--resume", action="store_true"' in s
    assert 'confirm = input(f"Type RESUME CT{CTID} to continue: ")' in s
    assert 'if confirm != f"RESUME CT{CTID}":' in s
    assert 'if not (args.resume or args.resume_ansible or args.resume_fresh):' in s
    assert 'create_runner(args.site, args.runner_cidr, args.gateway, template)' in s
    assert 'resume=args.resume' in s
    assert 'CT1231=VERIFIED_EXISTING_EMPTY_RUNNER' in s
    assert 're.fullmatch(r"local-lvm:vm-1231-disk-[0-9]+,size=16G"' in s
    assert 'pct_guest("test", "-f", "/opt/digitalhouses/bootstrap/semaphore-guest-install.py")' in s


def test_resume_is_rejected_for_existing_guests_with_partial_semaphore_state(monkeypatch):
    m=load()
    # Existing CT is correctly identified but Semaphore configuration already exists.
    config_lines=[
        "hostname: ansible-demo-office", "unprivileged: 1", "ostype: debian",
        "onboot: 0", "protection: 1", "cores: 1", "memory: 1024",
        "swap: 512", "features: nesting=1",
        "rootfs: local-lvm:vm-1231-disk-0,size=16G",
        "net0: name=eth0,bridge=vmbr0,ip=198.51.100.231/24,gw=198.51.100.1",
    ]
    def fake_run(*args,**kwargs):
        if args[:2]==("pct","config"): return "\n".join(config_lines)
        if args[:2]==("pct","status"): return "status: running"
        if args[:4]==("ip","-4","-o","addr"): return "inet 198.51.100.30/24"
        if args[:2]==("pvesm","status"): return "Name Type Status\nlocal dir active\nlocal-lvm lvmthin active"
        if args[:2]==("pveam","list"): return "debian-13-standard_13.6-1_amd64.tar.zst"
        return ""
    monkeypatch.setattr(m,"run",fake_run)
    monkeypatch.setattr(m,"pct_guest",lambda *a,**k: "ansible-demo-office" if a==("hostname",) else "198.51.100.231/24")
    monkeypatch.setattr(m,"code",lambda *a,**k: 1 if a[:2]==("qm","config") else (0 if a[:3]==("pct","exec","1231") else 1))
    monkeypatch.setattr(m.os,"geteuid",lambda:0)
    # This test documents rejection of detected installed marker, independent of runtime config mocks.
    import pathlib
    original_exists=pathlib.Path.exists
    monkeypatch.setattr(pathlib.Path,"exists", lambda p: True if str(p) in ("/etc/pve/user.cfg","/sys/class/net/vmbr0") else original_exists(p))
    import types
    monkeypatch.setattr(pathlib.Path,"is_file",lambda p: True if str(p).endswith("pve_host_write_ed25519") or str(p).endswith("pve-identities-home-pending.json") else False)
    # Skip dynamic read-only escrow conditions in this isolated test by using a non-pilot site.
    with pytest.raises(m.Blocked,match=r"partial Semaphore runtime: /etc/semaphore/config.json"):
        m.host_preflight("demo-office","198.51.100.231/24","198.51.100.1","198.51.100.30",resume=True)

def test_host_apt_refresh_rejects_partial_indexes():
    src=SRC.read_text()
    assert 'pct_guest("apt-get", "update", "-o", "APT::Update::Error-Mode=any"' in src
    assert '"Acquire::Retries=2"' in src


def test_resume_rejects_unexpected_mount_keys_regex():
    import re
    src=SRC.read_text()
    pattern=re.compile(r"(?:mp|net|hostpci|usb|scsi|sata|ide|virtio)\d+")
    for key in ("mp0","net1","hostpci0","usb0","scsi0"):
        assert pattern.fullmatch(key), key
    assert "and k != \"net0\"" in src
    assert r"(?:mp|net|hostpci|usb|scsi|sata|ide|virtio)\d+" in src


def test_etc_permissions_repair_is_strict_and_idempotent(monkeypatch):
    m=load()
    calls=[]
    values=iter(["700:0:0:directory", "755:0:0:directory"])
    def fake_guest(*args, **kwargs):
        calls.append(args)
        if args[:1] == ("stat",):
            return next(values)
        if args == ("chmod", "0755", "/etc"):
            return ""
        raise AssertionError(args)
    monkeypatch.setattr(m, "pct_guest", fake_guest)
    m.ensure_debian_etc_traversable()
    assert calls == [
        ("stat", "-c", "%a:%u:%g:%F", "/etc"),
        ("chmod", "0755", "/etc"),
        ("stat", "-c", "%a:%u:%g:%F", "/etc"),
    ]


def test_etc_permissions_repair_rejects_unknown_state_and_no_unnecessary_writes(monkeypatch):
    m=load()
    calls=[]
    def fake_guest(*args, **kwargs):
        calls.append(args)
        return "755:0:0:directory"
    monkeypatch.setattr(m, "pct_guest", fake_guest)
    m.ensure_debian_etc_traversable()
    assert len(calls) == 1
    monkeypatch.setattr(m, "pct_guest", lambda *args, **kwargs: "750:0:0:directory")
    with pytest.raises(m.Blocked, match="manual audit"):
        m.ensure_debian_etc_traversable()


def test_fresh_installer_stages_catalog_seeder_with_worker_required_permissions():
    src = SRC.read_text()
    body = src.split("def install_guest(site, runner_ip, checkout, *, resume_ansible=False):", 1)[1].split("\ndef stage_semaphore_catalog_sync(", 1)[0]
    assert 'pct_guest("chmod", "0644", seeder_path)' in body
    assert 'pct_guest("stat", "-c", "%u:%a", seeder_path) != "0:644"' in body
    assert body.index('pct_guest("chmod", "0644", seeder_path)') < body.index('guest_args =')


def test_etc_permissions_are_checked_before_apt():
    s=SRC.read_text()
    body=s.split("def install_guest(site, runner_ip, checkout, *, resume_ansible=False):",1)[1]
    assert body.index("ensure_debian_etc_traversable()") < body.index('pct_guest("apt-get", "update"')
    assert 'pct_guest("chmod", "0755", "/etc")' in s


def test_resume_ansible_is_distinct_from_empty_resume_and_requires_confirmation():
    s=SRC.read_text()
    assert 'parser.add_mutually_exclusive_group()' in s
    assert 'resume_group.add_argument("--resume-ansible"' in s
    assert 'Type RESUME ANSIBLE CT{CTID} to continue: ' in s
    assert 'if confirm != f"RESUME ANSIBLE CT{CTID}":' in s
    assert 'resume_ansible=args.resume_ansible' in s
    assert 'Existing Ansible version differs from release' in s
    assert 'CT1231=VERIFIED_EXISTING_ANSIBLE_ONLY' in s
    assert '"--resume-ansible"' in s
    assert 'if not (args.resume or args.resume_ansible or args.resume_fresh):' in s


def test_resume_semaphore_only_seeds_verified_running_service():
    src=SRC.read_text()
    assert 'resume_group.add_argument("--resume-semaphore", action="store_true"' in src
    assert 'Type COMPLETE SEMAPHORE CT{CTID} to continue: ' in src
    assert 'if confirm != f"COMPLETE SEMAPHORE CT{CTID}":' in src
    assert 'if not args.resume_semaphore:' in src
    assert 'CT1231=VERIFIED_RUNNING_SEMAPHORE' in src
    assert 'SEMAPHORE_ADMIN_API_AUTH=PASS' in src
    assert '"--admin-password-file"' in src
    assert '"/opt/digitalhouses/bootstrap/semaphore-project.py"' in src
    assert 'response.status==200' in src
    assert 'ctx=ssl.create_default_context(cafile=' in src
    assert '"/root/dh-fleet-deploy-key"' in src  # Forbidden resident credential remains gated
    assert '"NO_INFRASTRUCTURE_TASKS_LAUNCHED=YES" not in probe' in src


def test_resume_semaphore_mutually_exclusive_preflight_and_no_destructive_ops():
    src=SRC.read_text()
    assert 'resume_semaphore=args.resume_semaphore' in src
    assert 'if resume or resume_ansible or resume_semaphore or resume_fresh:' in src
    assert 'if not (args.resume or args.resume_ansible or args.resume_fresh):' in src
    assert 'if not args.resume_semaphore:' in src
    assert '"digitalhouses-semaphore.service") != "active"' in src
    assert 'unexpected deployment or package artifacts' in src
    assert '"pct", "destroy"' not in src


def test_fresh_pve_downloads_latest_official_debian13(monkeypatch, tmp_path):
    m = load()
    calls = []
    archive = tmp_path / "debian-13-standard_13.6-1_amd64.tar.zst"
    archive.write_bytes(b"x" * 10_000_001)
    original = m.Path

    def fake_run(*args, **kwargs):
        calls.append(args)
        if args[:2] == ("pveam", "available"):
            return ("system debian-13-standard_13.1-2_amd64.tar.zst 100\n"
                    "system debian-13-standard_13.6-1_amd64.tar.zst 120")
        if args[:2] == ("pveam", "list"):
            return ("local:vztmpl/debian-13-standard_13.6-1_amd64.tar.zst"
                    if any(a[:2] == ("pveam", "download") for a in calls) else "")
        return ""
    monkeypatch.setattr(m, "run", fake_run)
    monkeypatch.setattr(m, "Path", lambda p: tmp_path if str(p).startswith("/var/lib/vz/template/cache") else original(p))
    assert m.ensure_official_debian_template().endswith("13.6-1_amd64.tar.zst")
    assert ("pveam", "update") in calls
    assert ("pveam", "download", "local", "debian-13-standard_13.6-1_amd64.tar.zst") in calls


def test_template_cached_skips_download(monkeypatch, tmp_path):
    m = load()
    archive = tmp_path / "debian-13-standard_13.6-1_amd64.tar.zst"
    archive.write_bytes(b"x" * 10_000_001)
    calls = []
    def fake_run(*args, **kwargs):
        calls.append(args)
        if args[:2] == ("pveam", "available"):
            return "system debian-13-standard_13.6-1_amd64.tar.zst 120"
        if args[:2] == ("pveam", "list"):
            return "local:vztmpl/debian-13-standard_13.6-1_amd64.tar.zst"
        return ""
    monkeypatch.setattr(m, "run", fake_run)
    monkeypatch.setattr(m, "Path", lambda p: tmp_path)
    assert m.ensure_official_debian_template().startswith("local:vztmpl/")
    assert not any(a[:2] == ("pveam", "download") for a in calls)


def test_download_fails_closed_without_official_candidate(monkeypatch):
    m = load()
    calls = []
    def fake_run(*args, **kwargs):
        calls.append(args)
        return "" if args[:2] == ("pveam", "available") else ""
    monkeypatch.setattr(m, "run", fake_run)
    with pytest.raises(m.Blocked, match="Official Debian 13"):
        m.ensure_official_debian_template()
    assert not any(a[:2] == ("pveam", "download") for a in calls)


def test_download_after_authentication_and_strict_preflight_without_create_prompt():
    src = SRC.read_text()
    body = src.split("def apply(args, template):", 1)[1].split("\ndef main(", 1)[0]
    assert body.index("validate_fleet_password(") < body.index("verify_site_escrow(")
    assert body.index("verify_site_escrow(") < body.index("host_preflight(")
    assert body.index("host_preflight(") < body.index("ensure_official_debian_template()")
    assert body.index("ensure_official_debian_template()") < body.index("create_runner(")
    fresh_branch = body.split("        else:\n            # Fresh creation", 1)[1].split("        host_preflight(", 1)[0]
    assert "input(" not in fresh_branch
    assert "Type CREATE" not in body


def test_offline_etc_repaired_before_first_start(monkeypatch, tmp_path):
    m = load()
    rootfs = tmp_path / "rootfs"
    rootfs.mkdir()
    etc = rootfs / "etc"
    etc.mkdir(mode=0o700)
    etc.chmod(0o700)
    events = []
    original_path = m.Path
    def fake_path(value):
        return rootfs if str(value) == "/var/lib/lxc/1231/rootfs" else original_path(value)
    def fake_run(*args, **kwargs):
        events.append(args)
        return "status: stopped" if args[:2] == ("pct", "status") else ""
    monkeypatch.setattr(m, "Path", fake_path)
    monkeypatch.setattr(m, "run", fake_run)
    m.ensure_debian_etc_before_first_boot()
    assert etc.stat().st_mode & 0o7777 == 0o755
    assert ("pct", "mount", "1231") in events
    assert ("pct", "unmount", "1231") in events
    assert events.index(("pct", "mount", "1231")) < events.index(("pct", "unmount", "1231"))


def test_offline_etc_unmounts_on_unexpected_permissions(monkeypatch, tmp_path):
    m = load()
    rootfs = tmp_path / "rootfs"
    rootfs.mkdir()
    etc = rootfs / "etc"
    etc.mkdir()
    etc.chmod(0o750)
    events = []
    original_path = m.Path
    monkeypatch.setattr(m, "Path", lambda value:
                        rootfs if str(value) == "/var/lib/lxc/1231/rootfs"
                        else original_path(value))
    def fake_run(*args, **kwargs):
        events.append(args)
        return "status: stopped" if args[:2] == ("pct", "status") else ""
    monkeypatch.setattr(m, "run", fake_run)
    with pytest.raises(m.Blocked, match="Unexpected mounted guest /etc mode"):
        m.ensure_debian_etc_before_first_boot()
    assert ("pct", "unmount", "1231") in events
    assert etc.stat().st_mode & 0o7777 == 0o750


def test_preboot_permission_gate_precedes_lxc_start():
    src = SRC.read_text()
    body = src.split("def create_runner(site, cidr, gateway, template):", 1)[1].split("\ndef ensure_debian_etc_traversable", 1)[0]
    assert body.index("ensure_debian_etc_before_first_boot()") < body.index('run("pct", "start"')
    assert body.index('run("pct", "start"') < body.index("verify_fresh_system_bus()")


def test_fresh_system_bus_requires_healthy_systemd(monkeypatch):
    m = load()
    def fake_code(*args, **kwargs):
        return 1 if args[-2:] == ("systemctl", "is-system-running") else 0
    monkeypatch.setattr(m, "code", fake_code)
    monkeypatch.setattr(m, "pct_guest", lambda *a, **k: "ssh.service loaded failed failed SSH")
    monkeypatch.setattr(m.time, "sleep", lambda _: None)
    with pytest.raises(m.Blocked, match="readiness timeout"):
        m.verify_fresh_system_bus(attempts=2, delay_seconds=0)



def test_owner_type_site_metadata_is_required_before_guest_changes(tmp_path):
    m = load()
    checkout = tmp_path / "source"
    checkout.mkdir()
    site = tmp_path / "fleet-detail" / "inventories" / "demo-office" / "group_vars"
    site.mkdir(parents=True)
    file = site / "all.yml"
    file.write_text("---\nsite:\n  id: demo-office\n  owner_slug: demo\n"
                    "  type: office\n  name: CI Test\n")
    m.verify_site_identity(checkout, "demo-office")
    for invalid in ("demo", "demo_office", "other-office", "demo-home"):
        with pytest.raises(m.Blocked):
            m.verify_site_identity(checkout, invalid)
    file.write_text("---\nsite:\n  id: demo-office\n  owner_slug: other\n"
                    "  type: office\n")
    with pytest.raises(m.Blocked, match="mismatch"):
        m.verify_site_identity(checkout, "demo-office")
    file.write_text("---\nsite:\n  id: demo-office\n  owner_slug: demo\n"
                    "  type: office\n  type: office\n")
    with pytest.raises(m.Blocked, match="Duplicate"):
        m.verify_site_identity(checkout, "demo-office")
    file.write_text("---\nsite:\n  name: Invalid\nother:\n"
                    "  id: demo-office\n  owner_slug: demo\n  type: office\n")
    with pytest.raises(m.Blocked, match="Missing canonical"):
        m.verify_site_identity(checkout, "demo-office")


def test_canonical_site_guard_is_before_guest_writes():
    source = SRC.read_text()
    authenticate = source.split("def authenticate_git(",1)[1].split(
        "\ndef verify_site_identity(",1)[0]
    assert authenticate.index("fetch_site_inventory(") < authenticate.index(
        "verify_site_identity(")
    body = source.split("def apply(args, template):", 1)[1].split(
        "\ndef main(", 1)[0]
    assert body.index("authenticate_git(") < body.index("create_runner(")

def test_site_tree_rejects_sibling_scope_and_symlinks():
    m = load()
    fake_sha = "a" * 40
    entries = [
        {"path": f"inventories/home/{path}", "type": "blob",
         "mode": "100644", "sha": fake_sha, "size": 100}
        for path in ("hosts.yml", "runner.yml", "group_vars/all.yml")
    ]
    entries.append({"path": "inventories/other/hosts.yml",
                    "type": "blob", "mode": "100644", "sha": fake_sha, "size": 100})
    paths = m.validate_site_inventory_tree(entries, "home")
    assert len(paths) == 3 and all(p.startswith("inventories/home/") for p in paths)
    for bad in ({"path": "inventories/home/evil.py", "type": "blob",
                 "mode": "100644", "sha": fake_sha, "size": 100},
                {"path": "inventories/home/other.yml", "type": "blob",
                 "mode": "120000", "sha": fake_sha, "size": 100}):
        with pytest.raises(m.Blocked):
            m.validate_site_inventory_tree(entries + [bad], "home")
    with pytest.raises(m.Blocked):
        m.validate_site_inventory_tree(entries[:2], "home")


def test_bootstrap_never_creates_fleet_repo_access_in_guest():
    source = SRC.read_text()
    assert "def register_deploy_key(" not in source
    assert "FLEET_VAULT_ON_GUEST=NO" in source
    assert "FLEET_GITHUB_KEY_ON_GUEST=NO" in source
    assert "fetch_site_inventory(checkout, fleet_sha, env, site)" in source
    assert "install_site_inventory(args.site, site_files, checkout)" in source
    assert '"secrets/fleet.vault.yml", env' in source
    assert 'run("pct", "push", str(CTID), str(source), staging' in source
    assert '"secrets/fleet.vault.yml", timeout=30' not in source

def test_fresh_boot_waits_for_transient_systemd_rc3(monkeypatch):
    m = load()
    calls = []
    readings = iter([3, 0, 0, 0, 0, 0])
    monkeypatch.setattr(m, "code", lambda *args, **kw: calls.append(args) or next(readings))
    monkeypatch.setattr(m.time, "sleep", lambda seconds: None)
    m.verify_fresh_system_bus(attempts=3, delay_seconds=0)
    assert len(calls) == 6
    assert calls[0] == ("pct", "exec", "1231", "--", "systemctl", "is-active", "dbus.service")


def test_fresh_boot_readiness_timeout_preserves_failure(monkeypatch):
    m = load()
    monkeypatch.setattr(m, "code", lambda *args, **kw: 3)
    monkeypatch.setattr(m.time, "sleep", lambda seconds: None)
    with pytest.raises(m.Blocked, match="readiness timeout"):
        m.verify_fresh_system_bus(attempts=2, delay_seconds=0)


def test_resume_fresh_flag_requires_strict_empty_guest():
    src = SRC.read_text()
    assert 'resume_group.add_argument("--resume-fresh"' in src
    assert 'Type RESUME FRESH CT{CTID} to continue:' in src
    assert 'CT1231=VERIFIED_FRESH_PREBOOT_RUNNER' in src
    assert 'Fresh resume guest has deployment artifact: {marker}' in src
    assert 'resume_fresh=args.resume_fresh' in src
    assert 'if not (args.resume or args.resume_ansible or args.resume_fresh):' in src


def test_remote_commander_staged_only_and_never_installed_at_bootstrap():
    m = load()
    expected = {
        "playbooks/apply-runner-remote-commander.yml",
        "roles/runner_remote_commander/defaults/main.yml",
        "roles/runner_remote_commander/tasks/main.yml",
        "roles/runner_remote_commander/handlers/main.yml",
        "roles/runner_remote_commander/templates/digitalhouses-remote-commander.service.j2",
    }
    auxiliaries = {
        "bootstrap/remote-commander-install-request.sh",
        "bootstrap/dh-remote-commander-install.service",
        "bootstrap/dh-remote-commander-install.path",
    }
    assert set(m.REMOTE_INSTALL_FILES) == expected
    assert expected | auxiliaries <= set(m.CODE_FILES)
    assert m.REMOTE_BOOTSTRAP_DIR == "/opt/digitalhouses/bootstrap/remote-commander"
    body = SRC.read_text().split("def apply(args, template):", 1)[1].split("\ndef main(", 1)[0]
    assert body.index("install_site_inventory(") < body.index("seed_semaphore(")
    assert body.index("seed_semaphore(") < body.index("stage_runner_remote_commander(")
    assert 'REMOTE_COMMANDER_ON_DEMAND=READY' in SRC.read_text()
    assert "install_runner_remote_commander(" not in SRC.read_text()


def test_remote_commander_stages_fixed_root_watcher(monkeypatch, tmp_path):
    m = load()
    site = "demo-office"
    writes = []
    for relative in m.CODE_FILES:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("synthetic")
    def fake_guest(*args, **kwargs):
        writes.append(("guest", args))
        if args == ("hostname",):
            return m.runner_hostname(site)
        if args == ("systemctl", "is-active", "dh-remote-commander-install.path"):
            return "active"
        return ""
    def fake_run(*args, **kwargs):
        writes.append(("run", args))
        assert args[:3] == ("pct", "push", str(m.CTID))
        return ""
    monkeypatch.setattr(m, "pct_guest", fake_guest)
    monkeypatch.setattr(m, "run", fake_run)
    m.stage_runner_remote_commander(site, tmp_path)
    assert sum(1 for kind, args in writes if kind == "run") == 8
    assert not any(args[0] == "env" for kind, args in writes if kind == "guest")
    assert ("guest", ("systemctl", "enable", "--now",
                      "dh-remote-commander-install.path")) in writes


def test_remote_commander_rejects_bad_hostname_before_any_write(monkeypatch, tmp_path):
    m = load()
    monkeypatch.setattr(m, "pct_guest", lambda *a, **kw: "wrong-hostname")
    with pytest.raises(m.Blocked, match="hostname mismatch"):
        m.stage_runner_remote_commander("demo-office", tmp_path)


def test_site_escrow_is_private_fleet_data_not_hardcoded(monkeypatch, tmp_path):
    m = load()
    checkout = tmp_path / "source"
    checkout.mkdir()
    parent = tmp_path / "fleet-detail/inventories/demo-office"
    parent.mkdir(parents=True)
    manifest = parent / "runner.yml"
    manifest.write_text("---\nsite_runner:\n  bootstrap:\n"
                        "    repository_path: /opt/digitalhouses/dh_ansible\n"
                        "    pve_escrow_files:\n"
                        "      - escrow/ticket.json\n"
                        "      - keyfile\n")
    escrow = tmp_path / "escrow"
    (escrow / "escrow").mkdir(parents=True)
    (escrow / "escrow/ticket.json").write_text("synthetic")
    (escrow / "keyfile").write_text("synthetic")
    (escrow / "escrow/ticket.json").chmod(0o600)
    (escrow / "keyfile").chmod(0o600)
    monkeypatch.setattr(m, "ROOT_STAGE", escrow)

    # CI runs as an unprivileged user, whereas the real PVE escrow must be
    # root-owned. Simulate only the uid of synthetic escrow fixture paths;
    # leave the production ownership validation intact.
    original_stat = Path.stat

    class RootOwnedFixtureStat:
        st_uid = 0

        def __init__(self, real_stat):
            self._real_stat = real_stat

        def __getattr__(self, name):
            return getattr(self._real_stat, name)

    def synthetic_escrow_stat(path, *args, **kwargs):
        observed = original_stat(path, *args, **kwargs)
        if path == escrow or escrow in path.parents:
            return RootOwnedFixtureStat(observed)
        return observed

    monkeypatch.setattr(Path, "stat", synthetic_escrow_stat)
    m.verify_site_escrow(checkout, "demo-office")
    (escrow / "keyfile").unlink()
    with pytest.raises(m.Blocked, match="missing or unsafe"):
        m.verify_site_escrow(checkout, "demo-office")


def test_site_escrow_blocks_path_traversal(monkeypatch, tmp_path):
    m = load()
    (tmp_path / "source").mkdir()
    parent = tmp_path / "fleet-detail/inventories/demo-office"
    parent.mkdir(parents=True)
    (parent / "runner.yml").write_text(
        "---\nsite_runner:\n  bootstrap:\n"
        "    pve_escrow_files:\n      - ../outside\n"
    )
    monkeypatch.setattr(m, "ROOT_STAGE", tmp_path)
    with pytest.raises(m.Blocked, match="Unsafe relative site escrow path"):
        m.verify_site_escrow(tmp_path / "source", "demo-office")


def test_runner_hostname_uses_full_site_slug():
    m = load()
    assert m.runner_hostname("serzhan-home") == "ansible-serzhan-home"
    assert m.runner_hostname("amir-office") == "ansible-amir-office"
    assert m.runner_hostname("multi-owner-office") == "ansible-multi-owner-office"
    with pytest.raises(m.Blocked):
        m.runner_hostname("serzhan")
    with pytest.raises(m.Blocked):
        m.runner_hostname("serzhan_home")

def test_guest_failure_allows_safe_hostname_error_but_not_secret_output():
    m = load()
    stdout = "GUEST_LOCALE=PASS C.UTF-8\n"
    stderr = "INSTALL=BLOCKED: Runner hostname mismatch\nsecret=DO_NOT_SHOW"
    lines = m.safe_guest_failure_lines(
        ["pct", "exec", "1231", "--",
         "python3", "/opt/digitalhouses/bootstrap/semaphore-guest-install.py"],
        stdout, stderr,
    )
    assert "GUEST_LOCALE=PASS C.UTF-8" in lines
    assert "INSTALL=BLOCKED: Runner hostname mismatch" in lines
    assert not any("secret" in line for line in lines)

def test_safe_progress_of_guest_setup_is_allowlisted():
    m = load()
    raw = (
        "SEMAPHORE_PACKAGE=PASS\n"
        "SEMAPHORE_TLS_SETUP=START\n"
        "SEMAPHORE_TLS_SETUP=PASS\n"
        "SEMAPHORE_DB_MIGRATION=PASS\n"
        "SEMAPHORE_ADMIN_CREATE=PASS\n"
        "SEMAPHORE_SYSTEMD=PASS\n"
        "SECRET_MUST_NEVER_PRINT=unsafe\n"
    )
    argv = ("pct", "exec", "1231", "--", "python3",
            "/opt/digitalhouses/bootstrap/semaphore-guest-install.py")
    lines = m.safe_guest_failure_lines(argv, raw, "")
    assert len(lines) == 6
    assert lines[-1] == "SEMAPHORE_SYSTEMD=PASS"
    assert "SECRET_MUST_NEVER_PRINT" not in "\n".join(lines)




def test_inventory_file_existence_fails_closed_on_pct_129_and_symlink(monkeypatch):
    m = load()
    monkeypatch.setattr(m, "code", lambda *args, **kwargs: 129)
    with pytest.raises(m.Blocked, match="symlink or cannot be verified"):
        m.site_inventory_exists("/root/dh-site-upload-000")
    monkeypatch.setattr(m, "code", lambda *args, **kwargs: 0)
    with pytest.raises(m.Blocked, match="symlink"):
        m.site_inventory_exists("/root/dh-site-upload-000")
    def ordinary(*args, **kwargs):
        return 1 if args[-2] == "-L" else 129
    monkeypatch.setattr(m, "code", ordinary)
    with pytest.raises(m.Blocked, match="Cannot reliably determine"):
        m.site_inventory_exists("/root/dh-site-upload-000")


@pytest.mark.parametrize("stage_exists", [False, True])
def test_site_inventory_resume_recovers_verified_upload_after_sighup(
        monkeypatch, tmp_path, stage_exists):
    import hashlib
    m = load()
    code_dir = tmp_path / "code"
    code_dir.mkdir()
    source = tmp_path / "fleet-detail/inventories/demo-home/hosts.yml"
    source.parent.mkdir(parents=True)
    source.write_text("all:\\n  hosts:\\n    runner:\\n", encoding="utf-8")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    target = "/var/lib/ansible/site-data/demo-home/hosts.yml"
    staged = "/root/dh-site-upload-000"
    files = {}
    if stage_exists:
        files[staged] = ("root:root:600:regular file", digest)
    installs = []
    pushes = []

    def fake_code(*args, **kwargs):
        assert args[:5] == ("pct", "exec", "1231", "--", "test")
        if args[-2] == "-L":
            return 1
        if args[-2] == "-e":
            return 0 if args[-1] in files else 1
        raise AssertionError(args)

    def fake_guest(*args, **kwargs):
        if args[0] == "install" and "-d" in args:
            return ""
        if args[0] == "install" and "-o" in args:
            installs.append(args)
            if len(installs) == 1:
                raise m.Blocked("Command failed: pct, rc=129")
            files[target] = ("root:semaphore:640:regular file", digest)
            return ""
        if args[0] == "stat":
            return files[args[-1]][0]
        if args[0] == "sha256sum":
            return files[args[-1]][1] + "  " + args[-1]
        if args[0] == "rm":
            files.pop(args[-1], None)
            return ""
        raise AssertionError(args)

    def fake_run(*args, **kwargs):
        assert args[:3] == ("pct", "push", "1231")
        pushes.append(args)
        files[args[-1]] = ("root:root:600:regular file", digest)
        return ""

    monkeypatch.setattr(m, "code", fake_code)
    monkeypatch.setattr(m, "pct_guest", fake_guest)
    monkeypatch.setattr(m, "run", fake_run)
    monkeypatch.setattr(m.time, "sleep", lambda seconds: None)
    m.install_site_inventory("demo-home", ["inventories/demo-home/hosts.yml"], code_dir)
    assert len(installs) == 2
    assert len(pushes) == (0 if stage_exists else 1)
    assert files[target] == ("root:semaphore:640:regular file", digest)
    assert staged not in files


def test_inventory_resume_refuses_staging_hash_drift(monkeypatch, tmp_path):
    m = load()
    code_dir = tmp_path / "code"
    code_dir.mkdir()
    source = tmp_path / "fleet-detail/inventories/demo-home/hosts.yml"
    source.parent.mkdir(parents=True)
    source.write_text("all: {}\n", encoding="utf-8")
    monkeypatch.setattr(m, "code", lambda *args, **kwargs:
                        1 if args[-2] == "-L" else
                        0 if args[-1] == "/root/dh-site-upload-000" else 1)
    def guest(*args, **kwargs):
        if args[0] == "install" and "-d" in args:
            return ""
        if args[0] == "stat":
            return "root:root:600:regular file"
        if args[0] == "sha256sum":
            return "0" * 64 + "  " + args[-1]
        raise AssertionError(args)
    monkeypatch.setattr(m, "pct_guest", guest)
    with pytest.raises(m.Blocked, match="hash mismatch"):
        m.install_site_inventory("demo-home", ["inventories/demo-home/hosts.yml"], code_dir)


def test_public_launcher_accepts_controlled_pve_credential_restore_mode():
    launcher = (SRC.parent / "install.sh").read_text()
    assert '"$1" == "--resume-semaphore" && "$2" == "--restore-pve-access"' in launcher
    assert 'RESUME_ARGS=("$1" "$2")' in launcher
    assert "--restore-pve-access requires --resume-semaphore" in launcher


def test_semaphore_resume_restores_seeder_mode_and_pve_restore_can_be_requested():
    src = SRC.read_text()
    resume = src.split('if args.resume_semaphore:', 3)[-1]
    assert 'pct_guest("chmod", "0644", seeder_path)' in resume
    assert 'pct_guest("stat", "-c", "%u:%a", seeder_path)' in resume
    assert 'or (args.resume_semaphore and args.restore_pve_access)' in src
    assert 're.fullmatch(r"local-lvm:vm-1231-disk-[0-9]+,size=16G"' in src

def test_site_inventory_failure_emits_safe_command_and_step(monkeypatch, capsys):
    m = load()
    from types import SimpleNamespace
    def fake_subprocess(*args, **kwargs):
        return SimpleNamespace(returncode=129, stdout="PRIVATE=never-log",
                               stderr="SECRET=never-log")
    monkeypatch.setattr(m.subprocess, "run", fake_subprocess)
    with pytest.raises(m.Blocked, match="pct, rc=129"):
        m.run("pct", "exec", str(m.CTID), "--", "install",
              "-d", "-m", "0750", "/safe/example")
    out = capsys.readouterr().out
    assert "PCT_EXEC_FAILURE=command:install rc:129" in out
    assert "PRIVATE" not in out and "SECRET" not in out


def test_first_boot_fixes_only_postfix_lxc_namespace_failure(monkeypatch, capsys):
    m = load()
    systemd_repaired = {"value": False}
    commands = []
    def fake_code(*args, **kwargs):
        if args[-2:] == ("systemctl", "is-system-running"):
            return 0 if systemd_repaired["value"] else 1
        return 0
    def fake_guest(*args, **kwargs):
        commands.append(args)
        if args[:2] == ("systemctl", "list-units"):
            return "● postfix.service loaded failed failed Postfix Mail Transport Agent"
        if args == ("systemctl", "mask", "--now", "postfix.service"):
            return ""
        if args == ("systemctl", "reset-failed", "postfix.service"):
            systemd_repaired["value"] = True
            return ""
        raise AssertionError(args)
    monkeypatch.setattr(m, "code", fake_code)
    monkeypatch.setattr(m, "pct_guest", fake_guest)
    m.verify_fresh_system_bus(attempts=2, delay_seconds=0)
    output = capsys.readouterr().out
    assert "FRESH_DEBIAN_POSTFIX=MASKED_UNNEEDED_LXC_SERVICE" in output
    assert "FRESH_DEBIAN_DBUS=PASS" in output
    assert commands == [
        ("systemctl", "list-units", "--failed", "--all",
         "--no-legend", "--plain", "--no-pager"),
        ("systemctl", "mask", "--now", "postfix.service"),
        ("systemctl", "reset-failed", "postfix.service"),
    ]


def test_postfix_fix_never_masks_when_another_unit_failed(monkeypatch):
    m = load()
    calls = []
    def fake_guest(*args, **kwargs):
        calls.append(args)
        return ("● postfix.service loaded failed failed Postfix\n"
                "● other.service loaded failed failed Other")
    monkeypatch.setattr(m, "pct_guest", fake_guest)
    assert m.reconcile_first_boot_postfix_only() is False
    assert len(calls) == 1


def test_postfix_fix_never_relaxes_failed_dbus(monkeypatch):
    m = load()
    commands = []
    def fake_code(*args, **kwargs):
        return 1 if args[-2:] == ("systemctl", "is-active") else 1
    monkeypatch.setattr(m, "code", lambda *a, **kw: (
        1 if a[-2:] == ("is-active", "dbus.service") else 0))
    monkeypatch.setattr(m, "pct_guest", lambda *a, **kw: commands.append(a))
    monkeypatch.setattr(m.time, "sleep", lambda *_: None)
    with pytest.raises(m.Blocked, match="readiness timeout"):
        m.verify_fresh_system_bus(attempts=2, delay_seconds=0)
    assert commands == []



def test_resume_fresh_recovers_vault_pve_identity_without_reenrollment():
    """A failed first-boot retry still needs encrypted PVE SSH recovery."""
    source = SRC.read_text()
    body = source.split("def apply(args, template):", 1)[1].split(
        "\ndef commission_pve_preflight(", 1)[0]
    # Mature resume is excluded; empty fresh resume is intentionally INCLUDED.
    assert "if (not (args.resume or args.resume_ansible or args.resume_semaphore)" in body
    assert "or (args.resume_semaphore and args.restore_pve_access)):" in body
    assert "if not (args.resume or args.resume_fresh or args.resume_ansible" not in body
    assert "provision_pve_identity_stage(args.site, args.runner_cidr," in body
    assert body.index("stage_pve_access_audit(checkout)") < body.index(
        "provision_pve_identity_stage(args.site, args.runner_cidr,"
    )
    # The provisioner first decrypts and recovers existing credentials.
    provision = source.split("def provision_pve_identity_stage(", 1)[1].split(
        "\ndef apply(", 1)[0]
    assert 'if "PVE_FLEET_RECOVERY=PASS" in response:' in provision
    assert "PVE_CREDENTIALS=RESTORED_FROM_FLEET" in provision

def test_semaphore_resume_supports_explicit_restore_only_pve_access():
    src=SRC.read_text()
    assert 'parser.add_argument("--restore-pve-access", action="store_true"' in src
    assert "if args.restore_pve_access and not args.resume_semaphore:" in src
    assert 'or (args.resume_semaphore and args.restore_pve_access)' in src
    assert 'restore_only=bool(args.resume_semaphore)' in src
    assert 'if restore_only:' in src
    assert "PVE credential restore-only: canonical credentials absent" in src


def test_restore_only_cli_rejects_wrong_resume_mode():
    m=load()
    argv=["--site","demo-home","--runner-cidr","198.51.100.231/24",
          "--gateway","198.51.100.1","--pve-ip","198.51.100.30",
          "--restore-pve-access"]
    with pytest.raises(m.Blocked,match="requires --resume-semaphore"):
        m.main(argv)


def test_guest_directory_staging_retries_only_pct_129(monkeypatch):
    m = load()
    attempts = []
    def guest(*argv, **kwargs):
        attempts.append(argv)
        if len(attempts) < 3:
            raise m.Blocked("Command failed: pct, rc=129")
        return ""
    monkeypatch.setattr(m, "pct_guest", guest)
    monkeypatch.setattr(m.time, "sleep", lambda seconds: None)
    m.stage_guest_root_directory(
        "/opt/digitalhouses/bootstrap/remote-commander/roles",
        stage="remote_commander")
    assert len(attempts) == 3
    assert all(a[:6] == ("install", "-d", "-o", "root", "-g", "root") for a in attempts)
    assert all(a[-1].endswith("/roles") for a in attempts)


def test_guest_directory_staging_rejects_persistent_hup(monkeypatch):
    m = load()
    calls = []
    def guest(*args, **kwargs):
        calls.append(args)
        raise m.Blocked("Command failed: pct, rc=129")
    monkeypatch.setattr(m, "pct_guest", guest)
    monkeypatch.setattr(m.time, "sleep", lambda *_: None)
    with pytest.raises(m.Blocked, match="failed after 3 pct exec SIGHUP"):
        m.stage_guest_root_directory("/opt/digitalhouses/bootstrap/pve-access",
                                     stage="pve_access")
    assert len(calls) == 3


def test_guest_directory_staging_never_retries_other_errors(monkeypatch):
    m = load()
    calls = []
    def guest(*args, **kwargs):
        calls.append(args)
        raise m.Blocked("Command failed: pct, rc=1")
    monkeypatch.setattr(m, "pct_guest", guest)
    with pytest.raises(m.Blocked, match="rc=1"):
        m.stage_guest_root_directory("/opt/digitalhouses/bootstrap/pve-access",
                                     stage="pve_access")
    assert len(calls) == 1
    with pytest.raises(m.Blocked, match="Unsafe staged guest directory"):
        m.stage_guest_root_directory("/etc/ssh", stage="pve_access")
    assert len(calls) == 1


def test_pve_directory_and_remote_directory_staging_share_retry_policy():
    src = SRC.read_text()
    assert 'stage_guest_root_directory(folder, stage="remote_commander")' in src
    assert 'stage_guest_root_directory(folder, stage="pve_access")' in src
    assert 'stage_guest_root_directory(folder + "/playbooks", stage="pve_access")' in src
