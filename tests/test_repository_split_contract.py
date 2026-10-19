"""Contracts for the split Public Code / Private Fleet architecture."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_public_git_tree_contains_no_real_site_vault_or_inventory():
    assert not (ROOT / "secrets/fleet.vault.yml").exists()
    inventories = ROOT / "inventories"
    assert not inventories.exists() or all(
        (p.is_dir() and p.name == "example")
        or (p.is_file() and p.name == "README.md")
        for p in inventories.iterdir()
    )
    assert (ROOT / "tests/fixtures/syntax_inventory.yml").is_file()


def test_runner_requires_separate_private_checkout_and_pins_both_revisions():
    runner = (ROOT / "bin/ansimble-run").read_text()
    assert "DH_FLEET_DETAIL_DIR" in runner
    assert 'VAULT="$FLEET_ROOT/secrets/fleet.vault.yml"' in runner
    assert 'INVENTORY="$FLEET_ROOT/inventories/$SITE/hosts.yml"' in runner
    assert 'git -C "$FLEET_ROOT" fetch --quiet origin main' in runner
    assert 'CODE_HEAD=FAIL' in runner
    assert 'FLEET_HEAD=FAIL' in runner
    assert 'Unexpected Fleet Detail origin' in runner
    assert '$ROOT/secrets/fleet.vault.yml' not in runner


def test_bootstrap_fetches_code_and_fleet_from_independent_commits():
    script = (ROOT / "bootstrap/semaphore-host-install.py").read_text()
    assert 'CODE_REPO = "DigitalHouses/dh_ansible"' in script
    assert 'FLEET_REPO = "DigitalHouses/dh_ansimble_fleet_detail"' in script
    assert "--expected-commit" in script
    assert "--expected-fleet-commit" in script
    assert 'repository=CODE_REPO' in script
    assert 'repository=FLEET_REPO' in script
    assert 'checkout.parent / "fleet-detail"' in script
    assert "fetch_site_inventory(checkout, fleet_sha, env, site)" in script
    assert "install_site_inventory(args.site, site_files, checkout)" in script
    assert "def register_deploy_key(" not in script
    assert '"secrets/fleet.vault.yml",' not in script.split("CODE_FILES = (", 1)[1].split(")", 1)[0]


def test_release_requires_two_pinned_refs_and_never_starts_automatically():
    release = (ROOT / ".github/workflows/semaphore-installer-release.yml").read_text()
    assert "fleet_commit:" in release
    assert "--fleet-commit" in release
    assert "workflow_dispatch:" in release
    assert "\n  push:" not in release
    launcher = (ROOT / "bootstrap/semaphore-launch.sh").read_text()
    assert "FLEET_REV=__FLEET_COMMIT__" in launcher
    assert "--expected-fleet-commit" in launcher


def test_semaphore_public_playbook_private_inventory_separation():
    code = (ROOT / "bootstrap/semaphore-project.py").read_text()
    assert 'CODE_GIT_URL = "https://github.com/DigitalHouses/dh_ansible.git"' in code
    assert "FLEET_GIT_URL" not in code
    assert '"repository_id": code_repo["id"]' in code
    assert 'f"{SITE_INVENTORY_BASE}/{site}/hosts.yml"' in code
    assert '"type": "none"' in code
    assert '"type": "file"' in code
    assert '"type": "ssh"' not in code


def test_every_fleet_vault_consumer_uses_private_file_override():
    paths = [
        "roles/fleet_secrets/tasks/main.yml",
        "roles/truenas_base_apply/tasks/main.yml",
        "roles/truenas_base_recover/tasks/main.yml",
        "playbooks/apply-truenas-base.yml",
        "playbooks/set-backblaze-b2-credentials.yml",
        "playbooks/set-fleet-truenas-human-admin-password.yml",
        "playbooks/set-truenas-smb-password.yml",
    ]
    for path in paths:
        text = (ROOT / path).read_text()
        assert "{{ fleet_vault_file_override | mandatory }}" in text, path
        assert '{{ playbook_dir }}/../secrets/fleet.vault.yml' not in text, path
    wrapper = (ROOT / "bin/ansimble-run").read_text()
    assert wrapper.count('-e "fleet_vault_file_override=$VAULT"') == 2

def test_fleet_secrets_role_accepts_encrypted_site_passport_with_site_id_gate():
    role = (ROOT / "roles/fleet_secrets/tasks/main.yml").read_text()
    assert "Validate new site passport envelope" in role
    assert "site_passport.schema_version | int == 1" in role
    assert "site_passport.site_id == site.id" in role
    assert "site_passport.site_secrets is mapping" in role
    assert 'site_secrets: "{{ site_passport.site_secrets }}"' in role
    assert 'fleet_shared_secrets: "{{ site_passport.shared }}"' in role
    assert "site_passport is not defined" in role
    assert "fleet_secrets_compat_namespace" in role
    assert "no_log: true" in role



def test_restore_and_app_network_guards_never_embed_site_ips():
    for path in (
        "playbooks/accept-pve-backup-restore.yml",
        "roles/pve_backup_restore_isolation/tasks/main.yml",
        "roles/truenas_apps_ports/tasks/main.yml",
        "plugins/modules/truenas_apps_ports_reconcile.py",
    ):
        body = (ROOT / path).read_text()
        assert "192.168." not in body
    network = (ROOT / "roles/truenas_apps_ports/tasks/main.yml").read_text()
    assert "truenas_apps_ports.source_ip == truenas.host" in network
    module = (ROOT / "plugins/modules/truenas_apps_ports_reconcile.py").read_text()
    assert "validate_inventory(apps, pool, host)" in module
    assert "APP_MOUNT_OUTSIDE_POOL" in module
    assert "APP_WEB_PORTS_NOT_UNIQUE" in module


def test_retired_wizard_and_site_specific_code_are_not_in_public_snapshot():
    assert not (ROOT / "setup_wizard").exists()
    assert not (ROOT / "bootstrap/setup-wizard-pve.py").exists()
    assert not any(p.name.endswith("_backrest_b2") for p in (ROOT / "roles").iterdir())
    assert not any(p.name.endswith("_backrest_b2_reconcile.py") for p in (ROOT / "plugins/modules").iterdir())
    inventories = ROOT / "inventories"
    assert not inventories.exists() or all(
        (p.is_dir() and p.name == "example")
        or (p.is_file() and p.name == "README.md")
        for p in inventories.iterdir()
    )


def test_obsolete_setup_wizard_install_and_destructive_replacement_are_excluded():
    """Obsolete wizard-only installers must not reenter the universal codebase."""
    for path in (
        "bootstrap/runner-fresh-install.py",
        "bootstrap/runner-replace-e2e.py",
        "tests/test_runner_fresh_install.py",
        "tests/test_runner_replace_e2e.py",
    ):
        assert not (ROOT / path).exists(), path
