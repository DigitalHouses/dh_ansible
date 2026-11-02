"""Protect the MQTT endpoint extension of native Task05 and in-place Task00."""
import json
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[1]
TASK00 = yaml.safe_load((ROOT / "playbooks/request-semaphore-catalog-sync.yml").read_text())[1]
TASK05 = yaml.safe_load((ROOT / "playbooks/obtain-site-credentials.yml").read_text())[0]
CATALOG = json.loads((ROOT / "catalog/semaphore-tasks.json").read_text())
REGISTRY = json.loads((ROOT / "catalog/site-credentials.json").read_text())


def test_mqtt_endpoint_is_optional_text_and_next_to_broker_credentials():
    fields = next(t for t in CATALOG["tasks"] if t["title"] == "05-Ввести credentials объекта")["survey_vars"]
    assert len(fields) == 6
    names = [f["name"] for f in fields]
    j = names.index("DH_INPUT_MQTT_BROKER_HOST")
    assert names[j:j+3] == [
        "DH_INPUT_MQTT_BROKER_HOST",
        "DH_INPUT_MQTT_USERNAME",
        "DH_INPUT_MQTT_PASSWORD",
    ]
    assert fields[j]["type"] == "text"
    assert fields[j]["target"] == "env"
    assert fields[j]["required"] is False
    assert fields[j]["values"] == []
    assert not any("default" in f for f in fields)
    assert [f["survey_env"] for f in REGISTRY["manual"]] == names


def test_broker_host_is_stored_as_nonsecret_and_no_site_ip_is_hardcoded():
    host = next(f for f in REGISTRY["manual"] if f["id"] == "mqtt_broker_host")
    assert host["store"] == "variable_group_nonsecret"
    assert host["runtime_name"] == "DH_MQTT_BROKER_HOST"
    assert host["default_port"] == 1883
    yml = (ROOT / "playbooks/obtain-site-credentials.yml").read_text()
    assert "DH_INPUT_MQTT_BROKER_HOST" in yml
    assert "DH_MQTT_BROKER_HOST" in yml
    assert "| combine({'DH_MQTT_BROKER_HOST'" in yml
    assert "MQTT_BROKER_HOST_INVALID" in yml
    assert "DH_MQTT_BROKER_HOST" not in TASK05["vars"]["dh_secret_names"]
    assert "192.168." not in yml


def test_task00_refreshes_only_the_root_catalog_manager_before_sync():
    tasks = TASK00["tasks"]
    names = [t["name"] for t in tasks]
    inspect = names.index("Inspect vetted catalog manager bundled with this public checkout")
    validate = names.index("Require safe, regular catalog manager source")
    copy = names.index("Bring the root-owned catalog manager up to the reviewed Git release")
    sync = names.index("Ask the fixed root worker to synchronize the catalog")
    assert inspect < validate < copy < sync
    t = tasks[copy]
    assert t["become"] is True
    assert t["ansible.builtin.copy"]["src"] == "{{ playbook_dir }}/../bootstrap/semaphore-project.py"
    assert t["ansible.builtin.copy"]["dest"] == "/opt/digitalhouses/bootstrap/semaphore-project.py"
    assert t["ansible.builtin.copy"]["mode"] == "0644"
    assert "ansible.builtin.shell" not in str(tasks)


def test_no_pve_password_reset_or_mqtt_auth_claim_in_task05():
    yml = (ROOT / "playbooks/obtain-site-credentials.yml").read_text()
    assert "rejectattr('value', 'equalto', '')" in yml
    assert "other_fields: KEEP" in yml
    assert "verified_authentication: false" in yml
