"""Task07 private site repository contracts: no live GitHub writes in CI."""
import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
P = ROOT / "playbooks/provision-site-repository.yml"
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


def test_existing_repo_must_be_private_and_recognized_before_adoption():
    play = yaml.safe_load(P.read_text())[0]
    privacy = task(play, "Demand exact private repository identity")
    checks = privacy["ansible.builtin.assert"]["that"]
    assert any("private == true" in c for c in checks)
    assert any("full_name ==" in c for c in checks)
    existing = task(play, "Never adopt an unrelated or unmarked site repository")
    assert existing["when"] == "dh_git_before.status == 200"
    checks = existing["ansible.builtin.assert"]["that"]
    assert any("site.id" in str(c) and "site_id" in str(c) for c in checks)
    assert any("purpose" in str(c) for c in checks)


def test_new_marker_and_inventory_have_no_secret_values():
    play = yaml.safe_load(P.read_text())[0]
    marker = task(play, "Initialize identity marker only for new repository")
    inventory = task(play, "Initialize nonsecret site inventory only for new repository")
    assert marker["ansible.builtin.uri"]["method"] == "PUT"
    assert inventory["ansible.builtin.uri"]["method"] == "PUT"
    assert marker["no_log"] is True
    assert inventory["no_log"] is True
    assert "DH_GITHUB_PROVISION_TOKEN" not in str(marker["ansible.builtin.uri"]["body"])
    assert "DH_GITHUB_PROVISION_TOKEN" not in str(inventory["ansible.builtin.uri"]["body"])
    assert "dh_git_before.status == 404" in str(marker["when"])
    assert inventory["when"] == "dh_git_before.status == 404"
    for unwanted in ("DH_MQTT_PASSWORD", "DH_PVE_ROOT_PASSWORD", "DH_CLOUDFLARE_API_TOKEN"):
        assert unwanted not in P.read_text()


def test_adoption_does_not_force_push_or_delete():
    s = P.read_text()
    assert "force: true" not in s
    assert "method: DELETE" not in s
    assert "qm destroy" not in s
    assert "pct destroy" not in s
    assert "routine_read_only_access: NOT_COMMISSIONED" in s


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
