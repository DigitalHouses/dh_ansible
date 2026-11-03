"""Launcher release gate and secret-handling policy."""
import importlib.util
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1]
TEMPLATE=ROOT/"bootstrap/semaphore-launch.sh"
BUILD=ROOT/"bootstrap/build-semaphore-launcher.py"

def load():
    spec=importlib.util.spec_from_file_location("dh_build_launcher", BUILD)
    mod=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

def test_launcher_cannot_execute_without_immutable_release():
    mod=load()
    with pytest.raises(ValueError):
        mod.render(TEMPLATE.read_text(), "main", "b"*40)
    with pytest.raises(ValueError):
        mod.render(TEMPLATE.read_text(), "a"*40, "main")
    result=mod.render(TEMPLATE.read_text(), "a"*40, "b"*40).decode()
    assert "__RELEASE_COMMIT__" not in result
    assert "__FLEET_COMMIT__" not in result
    assert "FLEET_REV="+"b"*40 in result
    assert "--expected-fleet-commit" in result
    assert "repos/DigitalHouses/dh_ansible/contents/" in result
    assert "REV="+"a"*40 in result
    assert "--use-github-session" in result
    assert "https://github.com/login/device" in result

def test_no_mixed_web_ui_and_no_personal_token_in_guest():
    data=TEMPLATE.read_text()
    assert "setup-wizard" not in data
    assert "pct destroy" not in data
    assert "GH_CONFIG_DIR" in data
    assert "sha256sum -c" in data
    assert "GH_TOKEN=" not in data


def test_pve_default_route_parser_and_tar_validation_are_pipefail_safe():
    source=TEMPLATE.read_text()
    assert '$2=="via" {print $3}' in source
    assert 'grep -qx' not in source
    assert 'grep -x' in source
