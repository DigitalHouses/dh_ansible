"""Task07 private site repository contracts: no live GitHub writes in CI."""
import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
P = ROOT / "playbooks/provision-site-repository.yml"
RO = ROOT / "playbooks/tasks/site-repository-readonly-access.yml"
C = json.loads((ROOT / "catalog/semaphore-tasks.json").read_text())


def task(play, prefix):
    return next(t for t in play["tasks"] if t["name"].startswith(prefix))


def test_task07_catalog_and_source_parse():
    t = next(x for x in C["tasks"] if x["title"].startswith("07-"))
    assert t["playbook"] == "playbooks/provision-site-repository.yml"
    play = yaml.safe_load(P.read_text())
    assert len(play) == 1
    assert play[0]["hosts"] == "site_runner"
    assert play[0]["gather_facts"] is False
    assert play[0]["become"] is False
    assert "site-{{ site.id }}" == play[0]["vars"]["dh_git_name"]
    assert play[0]["vars"]["dh_git_owner"] == "DigitalHouses"


def test_create_is_private_user_account_and_missing_only():
    play = yaml.safe_load(P.read_text())[0]
    create = task(play, "Create only a missing PRIVATE site repository")
    api = create["ansible.builtin.uri"]
    assert api["url"] == "https://api.github.com/user/repos"
    assert api["method"] == "POST"
    assert api["body"]["private"] is True
    assert api["body"]["auto_init"] is True
    assert create["when"] == "dh_git_before.status == 404"
    assert create["no_log"] is True
    owner = task(play, "Verify authenticated GitHub owner")
    assert owner["ansible.builtin.uri"]["url"] == "https://api.github.com/user"
    assert owner["ansible.builtin.uri"]["method"] == "GET"
    assert "DH_GITHUB_PROVISION_TOKEN" not in str(create["ansible.builtin.uri"]["body"])
    defaults = play["module_defaults"]["ansible.builtin.uri"]
    assert defaults["validate_certs"] is True
    assert defaults["follow_redirects"] == "none"
    assert defaults["use_proxy"] is False


def test_create_failure_reports_only_safe_http_status_and_fixed_hints():
    play = yaml.safe_load(P.read_text())[0]
    create = task(play, "Create only a missing PRIVATE site repository")
    assert create["no_log"] is True
    codes = create["ansible.builtin.uri"]["status_code"]
    assert set([201, 401, 403, 409, 422, 429]).issubset(set(codes))

    check = task(play, "Require successful private repository creation")
    assert check["when"] == "dh_git_before.status == 404"
    hint = check["ansible.builtin.assert"]["fail_msg"]
    assert "dh_git_created.status" in hint
    assert "HTTP" in hint
    assert "Repository creation" in hint
    assert "Administration" in hint
    assert "422" in hint
    assert "dh_git_created.json" not in hint
    assert "dh_git_created.content" not in hint
    assert "DH_GITHUB_PROVISION_TOKEN" not in hint


def test_existing_repo_must_be_private_and_recognized_before_adoption():
    play = yaml.safe_load(P.read_text())[0]
    privacy = task(play, "Demand exact private repository identity")
    checks = privacy["ansible.builtin.assert"]["that"]
    assert any("private == true" in c for c in checks)
    assert any("full_name ==" in c for c in checks)
    existing = task(play, "Never adopt an unrelated or mismatched site marker")
    assert existing["when"] == ["dh_git_before.status == 200", "dh_git_marker_before.status == 200"]
    checks = existing["ansible.builtin.assert"]["that"]
    assert any("site.id" in str(c) and "site_id" in str(c) for c in checks)
    assert any("purpose" in str(c) for c in checks)


def test_unmarked_repository_recovery_requires_exact_pristine_auto_init():
    play = yaml.safe_load(P.read_text())[0]
    conditions = ["dh_git_before.status == 200", "dh_git_marker_before.status == 404"]
    for prefix, fragment in [
        ("Inspect initial commit", "/commits?"),
        ("Inspect full tree", "/git/trees/"),
        ("Verify initial README", "/contents/README.md"),
    ]:
        t = task(play, prefix)
        assert t["when"] == conditions
        assert t["no_log"] is True
        assert fragment in t["ansible.builtin.uri"]["url"]
        assert t["ansible.builtin.uri"]["method"] == "GET"

    strict = task(play, "Reject unmarked repository unless")
    assert strict["when"] == conditions
    checks = "\n".join(strict["ansible.builtin.assert"]["that"])
    for expected in (
        "default_branch == 'main'",
        "truncated",
        "tree | length",
        "README.md",
        "commits.json | length",
        "Initial commit",
        "parents | length",
        "author.login",
        "DigitalHouses private Ansible inventory",
    ):
        assert expected in checks
    assert strict["no_log"] is True
    assert task(play, "Approve recovery of a pristine initial repository")["when"] == conditions
    assert play["tasks"].index(strict) < play["tasks"].index(task(play, "Initialize identity marker"))
    assert "dh_git_pristine_recovery" in str(task(play, "Initialize identity marker")["when"])


def test_new_marker_and_inventory_have_no_secret_values():
    play = yaml.safe_load(P.read_text())[0]
    marker = task(play, "Initialize identity marker after fresh creation")
    inventory = task(play, "Initialize nonsecret site inventory after fresh creation")
    assert marker["ansible.builtin.uri"]["method"] == "PUT"
    assert inventory["ansible.builtin.uri"]["method"] == "PUT"
    assert marker["no_log"] is True
    assert inventory["no_log"] is True
    assert "DH_GITHUB_PROVISION_TOKEN" not in str(marker["ansible.builtin.uri"]["body"])
    assert "DH_GITHUB_PROVISION_TOKEN" not in str(inventory["ansible.builtin.uri"]["body"])
    assert "dh_git_before.status == 404" in str(marker["when"])
    assert "dh_git_pristine_recovery" in str(marker["when"])
    assert "dh_git_pristine_recovery" in str(inventory["when"])
    for unwanted in ("DH_MQTT_PASSWORD", "DH_PVE_ROOT_PASSWORD", "DH_CLOUDFLARE_API_TOKEN"):
        assert unwanted not in P.read_text()


def test_adoption_does_not_force_push_or_delete():
    s = P.read_text()
    assert "force: true" not in s
    assert "method: DELETE" not in s
    assert "qm destroy" not in s
    assert "pct destroy" not in s
    assert "dh_site_repository_access_status" in s
    assert "tasks/site-repository-readonly-access.yml" in s


def test_readiness_only_proves_github_owner_not_create_rights():
    play = yaml.safe_load((ROOT / "playbooks/check-site-credentials.yml").read_text())[0]
    probe = task(play, "Check GitHub authenticated identity without creating repositories")
    uri = probe["ansible.builtin.uri"]
    assert uri["method"] == "GET"
    assert uri["url"] == "https://api.github.com/user"
    assert probe["no_log"] is True
    assert "DH_GITHUB_PROVISION_TOKEN" in uri["headers"]["Authorization"]
    report = yaml.safe_load((ROOT / "playbooks/check-site-credentials.yml").read_text())[-1]["tasks"][-2]
    msg = report["ansible.builtin.debug"]["msg"]
    assert msg["github_repository_creation_permission"] == "NOT_VERIFIED_READ_ONLY"


def test_routine_access_is_native_git_and_semaphore_not_another_credential_store():
    play = yaml.safe_load(P.read_text())[0]
    routine = task(play, "Commission routine private repository access")
    assert routine["ansible.builtin.import_tasks"] == "tasks/site-repository-readonly-access.yml"
    idx = play["tasks"].index(routine)
    assert idx > play["tasks"].index(task(play, "Verify site inventory"))
    assert idx < play["tasks"].index(task(play, "Report safe site repository result"))
    msg = task(play, "Report safe site repository result")["ansible.builtin.debug"]["msg"]
    assert "dh_site_repository_access_status" in msg["routine_read_only_access"]
    assert yaml.safe_load(RO.read_text())


def test_site_ed25519_generated_in_memory_imported_to_native_semaphore_key_store():
    ts = yaml.safe_load(RO.read_text())
    generate = next(t for t in ts if t["name"].startswith("Generate a fresh Ed25519"))
    assert generate["no_log"] is True
    cmd = generate["ansible.builtin.command"]["argv"]
    assert cmd[0] == "{{ ansible_playbook_python }}"
    assert cmd[1].endswith("/scripts/generate-site-git-ed25519.py")

    store = next(t for t in ts if t["name"].startswith("Store Ed25519 SSH private key"))
    api = store["ansible.builtin.uri"]
    assert api["method"] == "POST"
    assert api["body"]["type"] == "ssh"
    assert api["body"]["ssh"]["login"] == "git"
    assert "dh_site_key_material.stdout" in api["body"]["ssh"]["private_key"]
    assert store["no_log"] is True
    assert store["when"] == "dh_site_named_keys | length == 0"

    txt = RO.read_text()
    assert "generate_ssh_key: true" not in txt
    assert "ssh-keygen" not in txt
    assert "mktemp" not in txt
    assert "copy:" not in txt
    assert "write_file" not in txt
    # The only permitted GitHub deletion is scoped revocation of the
    # exact obsolete read-only key after a verified fresh runner reset.
    assert txt.count("method: DELETE") == 1
    assert 'url: "{{ dh_git_api }}/keys/{{ dh_site_named_deploy_keys[0].id }}"' in txt


def test_ed25519_generation_round_trip_matches_ssh_public_key_without_file():
    import importlib.util

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    source = ROOT / "scripts/generate-site-git-ed25519.py"
    spec = importlib.util.spec_from_file_location("site_git_ed25519", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    first = module.create_keypair()
    second = module.create_keypair()
    assert first["public_key"].startswith("ssh-ed25519 ")
    assert first["public_key"] != second["public_key"]
    assert first["private_key"].startswith("-----" + "BEGIN OPENSSH PRIVATE KEY-----")

    loaded = serialization.load_ssh_private_key(first["private_key"].encode(), password=None)
    assert isinstance(loaded, Ed25519PrivateKey)
    assert loaded.public_key().public_bytes(
        encoding=serialization.Encoding.OpenSSH,
        format=serialization.PublicFormat.OpenSSH,
    ).decode() == first["public_key"]


def test_public_ssh_key_is_declarative_site_data_and_not_secret():
    ts = yaml.safe_load(RO.read_text())
    save = next(t for t in ts if t["name"].startswith("Commit only the nonsecret public key"))
    api = save["ansible.builtin.uri"]
    assert api["method"] == "PUT"
    assert "{{ dh_git_api }}/contents/{{ dh_site_public_file }}" == api["url"]
    assert "public_key" in api["body"]["content"]
    assert "private_key" not in api["body"]["content"]
    assert save["no_log"] is True

    guarded = next(t for t in ts if t["name"].startswith("Refuse unverified, foreign"))
    assert "dh_site_old_public_key" in str(guarded)
    assert "dh_site_old_deploy_keys[0].read_only == true" in str(guarded)
    assert "GITHUB_SITE_ED25519_KEY_PAIR_INCOMPLETE_REFUSE_ROTATION" in str(guarded)

    play = yaml.safe_load(P.read_text())[0]
    assert play["vars"]["dh_site_public_file"] == ".digitalhouses/semaphore-deploy-ed25519.pub"
    assert "Ed25519" in play["vars"]["dh_site_key_name"]


def test_site_deploy_key_is_strict_read_only_and_idempotent():
    ts = yaml.safe_load(RO.read_text())
    deploy = next(t for t in ts if t["name"].startswith("Register one read-only deploy key"))
    api = deploy["ansible.builtin.uri"]
    assert api["method"] == "POST"
    assert api["url"] == "{{ dh_git_api }}/keys"
    assert api["body"]["read_only"] is True
    assert deploy["when"] == "dh_site_named_deploy_keys | length == 0 or dh_site_recover_fresh_runner"
    assert deploy["no_log"] is True
    for prefix in (
        "Read current deploy keys",
        "Read back registered GitHub deploy key",
    ):
        item = next(t for t in ts if t["name"].startswith(prefix))
        assert item["ansible.builtin.uri"]["method"] == "GET"
        assert item["no_log"] is True
    check = next(t for t in ts if t["name"].startswith("Require exactly one matching read-only deploy key"))
    assert any("read_only == true" in x for x in check["ansible.builtin.assert"]["that"])
    assert any("public_key" in x for x in check["ansible.builtin.assert"]["that"])


def test_site_repo_bound_only_to_correct_local_semaphore_project_and_key():
    ts = yaml.safe_load(RO.read_text())
    projects = next(t for t in ts if t["name"].startswith("Restrict registration to exactly"))
    assert "site.id" in str(projects)
    create = next(t for t in ts if t["name"].startswith("Register private site repository"))
    assert create["no_log"] is True
    assert create["when"] == "dh_site_named_semaphore_repos | length == 0"
    body = create["ansible.builtin.uri"]["body"]
    assert body["git_url"] == "{{ dh_site_git_ssh_url }}"
    assert body["git_branch"] == "main"
    assert body["ssh_key_id"] == "{{ dh_site_ssh_key_id }}"
    verify = next(t for t in ts if t["name"].startswith("Verify exactly one private site repository binding"))
    assert any("ssh_key_id" in x for x in verify["ansible.builtin.assert"]["that"])
    assert any("git_url" in x for x in verify["ansible.builtin.assert"]["that"])
    assert any("project_id" in x for x in verify["ansible.builtin.assert"]["that"])
    assert ts[-1]["ansible.builtin.set_fact"]["dh_site_repository_access_status"] == "VERIFIED_NATIVE_SSH_CLONE"


def test_task07_uses_native_semaphore_git_ls_remote_and_clone_before_success():
    ts = yaml.safe_load(RO.read_text())
    branches = next(x for x in ts if x["name"].startswith("Probe private site SSH remote branches"))
    clone = next(x for x in ts if x["name"].startswith("Probe actual private site SSH checkout"))
    for operation, suffix in (
        (branches, "/branches"),
        (clone, "/playbooks?branch=main&app=ansible"),
    ):
        api = operation["ansible.builtin.uri"]
        assert api["method"] == "GET"
        assert api["url"].endswith(suffix)
        assert "dh_site_registered_semaphore_repos[0].id" in api["url"]
        assert "dh_site_semaphore_token.content" in api["headers"]["Authorization"]
        assert api["ca_path"] == "/etc/semaphore/tls.crt"
        assert operation["no_log"] is True
        assert operation["failed_when"] is False
        assert operation["changed_when"] is False

    remote_check = next(x for x in ts if x["name"].startswith("Require authenticated SSH ls-remote"))
    assert "'main' in dh_site_native_branches.json" in remote_check["ansible.builtin.assert"]["that"]
    clone_check = next(x for x in ts if x["name"].startswith("Require successful Semaphore Git CloneOrPull"))
    assert any("dh_site_native_clone.status" in x for x in clone_check["ansible.builtin.assert"]["that"])
    assert ts.index(remote_check) > ts.index(branches)
    assert ts.index(clone_check) > ts.index(clone)
    assert ts.index(ts[-1]) > ts.index(clone_check)
    assert ts[-1]["ansible.builtin.set_fact"]["dh_site_repository_access_status"] == "VERIFIED_NATIVE_SSH_CLONE"
    # GitHub source and private site files are left untouched on every repeat.
    # Only the precisely verified previous deploy key may be revoked after a
    # fresh runner reset. Repository and unrelated-key deletion stay forbidden.
    assert RO.read_text().count("method: DELETE") == 1
    assert 'url: "{{ dh_git_api }}/keys/{{ dh_site_named_deploy_keys[0].id }}"' in RO.read_text()
    assert "GIT_SSH_COMMAND" not in RO.read_text()


def test_clean_runner_rotation_requires_exact_old_site_key_before_revocation():
    ts = yaml.safe_load(RO.read_text())
    find = lambda start: next(i for i, t in enumerate(ts) if t["name"].startswith(start))
    early = find("Inspect existing deploy keys before")
    prior = find("Classify prior site public")
    gate = find("Refuse unverified, foreign")
    gen = find("Generate a fresh Ed25519")
    store = find("Store Ed25519 SSH private key")
    update = find("Commit only the nonsecret public key")
    verify = find("Verify native Semaphore SSH identity")
    revoke = find("Revoke only the verified former")
    deploy = find("Register one read-only deploy key")
    assert early < prior < gate < gen < store < update < verify < revoke < deploy

    checks = ts[gate]["ansible.builtin.assert"]["that"]
    assert any("dh_site_old_deploy_keys | length <= 1" in c for c in checks)
    assert any("dh_site_old_public_key.split()[:2]" in c for c in checks)
    assert any("dh_site_old_deploy_keys[0].read_only == true" in c for c in checks)
    assert any("dh_site_named_keys | length == 0" in c for c in checks)

    public = ts[update]
    assert public["ansible.builtin.uri"]["method"] == "PUT"
    assert public["ansible.builtin.uri"]["status_code"] == [200, 201]
    assert "dh_site_pub_before.json.sha" in public["ansible.builtin.uri"]["body"]["sha"]
    assert public["when"] == "dh_site_named_keys | length == 0"
    assert public["no_log"] is True

    delete = ts[revoke]
    assert delete["ansible.builtin.uri"]["method"] == "DELETE"
    assert delete["ansible.builtin.uri"]["status_code"] == 204
    assert delete["ansible.builtin.uri"]["url"] == (
        "{{ dh_git_api }}/keys/{{ dh_site_named_deploy_keys[0].id }}")
    assert delete["when"] == [
        "dh_site_recover_fresh_runner",
        "dh_site_named_deploy_keys | length == 1",
    ]
    assert delete["no_log"] is True
    assert not any(t.get("ansible.builtin.uri", {}).get("method") == "DELETE"
                   and t is not delete for t in ts)
