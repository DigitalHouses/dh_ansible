"""Offline tests for site-declared, tightly bounded TrueNAS app web publication."""
from __future__ import annotations

from copy import deepcopy
import importlib.util
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).resolve().parents[1] / "plugins/modules/truenas_apps_ports_reconcile.py"
HOST = "198.51.100.15"  # RFC 5737 documentation-only address
POOL = "example_pool"


def load():
    spec = importlib.util.spec_from_file_location("truenas_apps_ports_generic", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def app(name, port, user):
    return {
        "name": name, "catalog_app": name, "train": "community",
        "version": "1.0.0",
        "values": {
            "run_as": {"user": user, "group": 3000},
            "network": {
                "host_network": False, "networks": [],
                "web_port": {"bind_mode": "published", "port_number": port,
                             "host_ips": [HOST]},
            },
            "storage": {"additional_storage": [{
                "host_path_config": {"path": f"/mnt/{POOL}/data"},
                "mount_path": "/data", "read_only": True,
            }]},
        },
    }


def apps():
    return [app("example-browser", 30200, 1001),
            app("example-backup", 30201, 1002)]


def test_two_apps_derived_from_declarative_site_data():
    m = load()
    declared = apps()
    specs = m.validate_inventory(declared, POOL, HOST)
    assert set(specs) == {"example-browser", "example-backup"}
    assert specs["example-browser"]["port"] == 30200
    observed = {"state": "RUNNING", "version": "1.0.0"}
    before = deepcopy(declared[0]["values"])
    before["network"]["web_port"] = {
        "bind_mode": "exposed", "port_number": 30200}
    assert m.decide(before, declared[0], observed,
                    specs["example-browser"], HOST) == "UPDATE"
    assert m.decide(declared[0]["values"], declared[0], observed,
                    specs["example-browser"], HOST) == "NOOP"


def test_wrong_app_or_site_data_blocks_without_network_call():
    m = load()
    valid = apps()
    with pytest.raises(ValueError, match="APP_SCOPE_NOT_EXACT"):
        m.validate_inventory(valid[:1], POOL, HOST)
    duplicate = deepcopy(valid)
    duplicate[1]["name"] = duplicate[0]["name"]
    with pytest.raises(ValueError, match="APP_NAMES_NOT_UNIQUE"):
        m.validate_inventory(duplicate, POOL, HOST)
    duplicate_port = deepcopy(valid)
    duplicate_port[1]["values"]["network"]["web_port"]["port_number"] = 30200
    with pytest.raises(ValueError, match="APP_WEB_PORTS_NOT_UNIQUE"):
        m.validate_inventory(duplicate_port, POOL, HOST)
    wrong_pool = deepcopy(valid)
    wrong_pool[0]["values"]["storage"]["additional_storage"][0]["host_path_config"]["path"] = "/mnt/another_pool/data"
    with pytest.raises(ValueError, match="APP_MOUNT_OUTSIDE_POOL"):
        m.validate_inventory(wrong_pool, POOL, HOST)
    wrong_host = deepcopy(valid)
    wrong_host[0]["values"]["network"]["web_port"]["host_ips"] = ["198.51.100.200"]
    with pytest.raises(ValueError, match="APP_DESIRED_PORT_NOT_EXACT"):
        m.validate_inventory(wrong_host, POOL, HOST)


def test_unapproved_runtime_drift_cannot_be_silently_adopted():
    m = load()
    selected = apps()[0]
    spec = m.validate_inventory(apps(), POOL, HOST)[selected["name"]]
    config = deepcopy(selected["values"])
    observed = {"state": "RUNNING", "version": "1.0.0"}
    config["network"]["web_port"] = {
        "bind_mode": "published", "port_number": spec["port"],
        "host_ips": ["198.51.100.200"]}
    with pytest.raises(ValueError, match="APP_WEB_PORT_OUTSIDE_EXACT_TRANSITION"):
        m.decide(config, selected, observed, spec, HOST)
    config = deepcopy(selected["values"])
    config["run_as"]["user"] += 1
    with pytest.raises(ValueError, match="APP_PROCESS_IDENTITY_DRIFT"):
        m.decide(config, selected, observed, spec, HOST)
