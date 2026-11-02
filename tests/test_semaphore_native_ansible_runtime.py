"""Real Ansible evaluation of Task 05 Jinja predicates before API writes.

Ansible --syntax-check does NOT render assert strings. The first live Task 05
failure was the delimiter {% inside a quoted regular expression.
"""
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

import yaml

ROOT = Path(__file__).resolve().parents[1]
PLAYBOOK = ROOT / "playbooks" / "obtain-site-credentials.yml"


def tasks():
    return yaml.safe_load(PLAYBOOK.read_text(encoding="utf-8"))[0]["tasks"]


def find_task(name):
    return next(t for t in tasks() if t["name"] == name)


def run_ansible(play):
    binary = shutil.which("ansible-playbook")
    assert binary, "ansible-core required for live Jinja validation gate"
    with tempfile.TemporaryDirectory(prefix="dh-task05-jinja-test-") as folder:
        path = Path(folder) / "assertions.yml"
        path.write_text(yaml.safe_dump([play], sort_keys=False))
        result = subprocess.run(
            [binary, "-i", "localhost,", "-c", "local", str(path)],
            text=True, capture_output=True, timeout=70, check=False,
        )
    assert result.returncode == 0, (
        "Real Ansible predicate evaluation failed; credentials were NOT written.\n"
        + result.stdout[-2500:] + "\n" + result.stderr[-800:]
    )


def test_task05_forbidden_delimiters_and_acceptance_regex():
    checks = find_task("Validate submitted inputs before API writes")
    expressions = checks["ansible.builtin.assert"]["that"]
    assert len(expressions) == 3
    guard = expressions[2]
    # Any Jinja opener in the literal expression gets parsed before regex.
    assert not any(token in guard for token in ("{{", "{%", "{#"))
    match = re.search(r"regex_search\('([^']+)'\)", guard)
    assert match, "Guard must use a regular expression"
    expr = re.compile(match.group(1))
    for unsafe in ("p{{x}}", "p{% include %}", "p{# comment #}"):
        assert expr.search(unsafe) is not None
    for allowed in ("abc123!", "long#pass", "p{safe}", "a{?}", "Мой пароль"):
        assert expr.search(allowed) is None


def test_task05_real_ansible_executes_validation_before_api_calls():
    checks = find_task("Validate submitted inputs before API writes")
    run_ansible({
        "name": "Runtime-evaluate Task 05 input validator (dummy only)",
        "hosts": "localhost", "gather_facts": False,
        "tasks": [{
            "name": "Run Task 05 native Jinja assertions",
            "ansible.builtin.assert": checks["ansible.builtin.assert"],
            "loop": [
                {"key": "DH_PVE_ROOT_PASSWORD", "value": "dummy-!@56"},
                {"key": "DH_MQTT_PASSWORD", "value": "dummy{ok}"},
                {"key": "DH_CLOUDFLARE_API_TOKEN", "value": "dummy#123"},
            ],
            "no_log": True,
        }],
    })


def test_task05_real_ansible_verifies_saved_secret_names():
    # Covers another expression not evaluated by --syntax-check:
    # Ansible's is superset test on stored Variable Group metadata.
    final = find_task("Save provided values in the existing Semaphore Variable Group")
    metadata = next(t for t in final["block"]
                    if t["name"] == "Confirm stored names and nonsecret username")
    run_ansible({
        "name": "Runtime-evaluate Task 05 post-write checks (dummy metadata)",
        "hosts": "localhost", "gather_facts": False,
        "vars": {
            "dh_after": {"json": {
                "id": 17, "secrets": [{"name": "DH_PVE_ROOT_PASSWORD"}],
                "env": '{"DH_MQTT_USERNAME":"dummy-login","DH_MQTT_BROKER_HOST":"broker.example.test"}',
            }},
            "dh_groups": [{"id": 17}],
            "dh_secret_operations": [{"name": "DH_PVE_ROOT_PASSWORD"}],
            "dh_supplied": {"DH_MQTT_USERNAME": "dummy-login", "DH_MQTT_BROKER_HOST": "broker.example.test"},
        },
        "tasks": [{
            "name": "Run exact post-write contract predicates",
            "ansible.builtin.assert": metadata["ansible.builtin.assert"],
            "no_log": True,
        }],
    })


def test_task05_real_ansible_validates_broker_host_before_api_call():
    check = find_task(
        "Validate the supplied broker IPv4/DNS endpoint"
    )
    assert check["no_log"] is True
    run_ansible({
        "name": "Runtime-evaluate valid site MQTT broker input (dummy only)",
        "hosts": "localhost", "gather_facts": False,
        "vars": {"dh_supplied": {"DH_MQTT_BROKER_HOST": "broker.example.test"}},
        "tasks": [{
            "name": "Validate broker input",
            "ansible.builtin.assert": check["ansible.builtin.assert"],
            "no_log": True,
        }],
    })
