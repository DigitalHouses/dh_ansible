"""Native name discovery is opt-in per site, uses upstream plugins only."""
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[1]

def test_native_name_resolvers_reuse_upstream_plugins_without_external_scripts():
    tasks = yaml.safe_load((ROOT / "roles/netalertx_native/tasks/name_resolution.yml").read_text())
    assert isinstance(tasks, list)
    text = (ROOT / "roles/netalertx_native/tasks/name_resolution.yml").read_text()
    for plugin in ("NSLOOKUP", "AVAHISCAN", "NBTSCAN", "DIGSCAN"):
        assert plugin in text
    assert "LOADED_PLUGINS" in text
    assert "netalertx_native_existing_plugins +" in text
    assert "netalertx_native_name_resolution" in text
    assert "from_yaml" in text
    assert "difference(netalertx_native_existing_plugins)" in text
    assert "ansible.builtin.lineinfile" in text
    assert "/app/config/app.conf" in text
    assert "UPDATE Devices" not in text
    assert "/app/front/plugins" not in text
    assert "docker" not in text.lower()

def test_resolvers_are_site_scoped_and_trigger_backend_restart_only_if_changed():
    play = (ROOT / "playbooks/deploy-netalertx-pve-from-worker.yml").read_text()
    role = yaml.safe_load((ROOT / "roles/netalertx_native/tasks/main.yml").read_text())
    verify = (ROOT / "roles/netalertx_native/tasks/verify.yml").read_text()
    assert "netalertx.application.name_resolution" in play
    imports = [step["ansible.builtin.import_tasks"] for step in role]
    assert imports.index("install.yml") < imports.index("name_resolution.yml")
    assert imports.index("name_resolution.yml") < imports.index("verify.yml")
    assert "netalertx_native_name_config_changed" in verify
    assert "restarted" in verify


def test_loaded_plugin_parser_captures_exactly_one_line():
    import re
    text = (ROOT / "roles/netalertx_native/tasks/name_resolution.yml").read_text()
    assert "regex_findall('(?m)^LOADED_PLUGINS=(.*)$')" in text
    for sample in (
        'LOADED_PLUGINS=["ARPSCAN","UI"]\\nNSLOOKUP_RUN="disabled"\\n',
        "LOADED_PLUGINS=['ARPSCAN', 'UI']\\nNSLOOKUP_RUN='disabled'\\n",
    ):
        values = re.findall(r"(?m)^LOADED_PLUGINS=(.*)$", sample)
        assert len(values) == 1
        assert yaml.safe_load(values[0]) == ["ARPSCAN", "UI"]
