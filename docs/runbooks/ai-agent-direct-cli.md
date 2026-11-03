# AI Agent Direct Ansible CLI — live-tested route for ChatGPT (2026-11-03)

**Status:** LIVE PASS for **non-secret read-only Task 20 smoke**, from an ordinary ChatGPT conversation via Remote Desktop Commander.  
**Scope:** site runner `ansible-serzhan-home` (CT1231), site `serzhan-home`.  
**Main operations guide:** [ai-agent-execution.md](ai-agent-execution.md).

> This document records a tested **direct Ansible execution** route. It is **not** an authenticated Semaphore task launcher and **does not** create a task in Semaphore history or automatically inject Semaphore Variable Groups / secrets.

## Verified live facts

- Remote Desktop Commander device: `ansible-serzhan-home`, **Online**.
- Remote terminal executes as root; ordinary Ansible execution is explicitly switched to the Linux `semaphore` principal with **`runuser -u semaphore --`**; `id` returned UID 999.
- Ansible: `/opt/ansible/venv/bin/ansible-playbook`, **ansible-core 2.21.4**.
- Inventory: `/var/lib/ansible/site-data/serzhan-home/hosts.yml`, readable by `semaphore`. `ansible-inventory --graph` showed only `runner` under `site_runner`.
- Independent checkout: `/var/lib/semaphore/dh-agent-checkout`, cloned as `semaphore`, `semaphore:semaphore 0700`. During acceptance it pointed to `df12a5b849b056b5450b7195ef6f2fceca8998ed`; always recheck SHA.
- **Actual direct execution:** `playbooks/smoke-semaphore.yml` completed `runner ok=1, changed=0, failed=0` (no PVE/VM/disk writes). This is a CLI smoke equivalent to the catalog's №20; there was **no** Semaphore Task #20 created.
- Ansible log file: `/var/lib/semaphore/dh-agent-smoke-20261103.log`; owner `semaphore:semaphore`, permissions **0600**, content successfully read using Remote Desktop Commander `read_file` after execution.

## Runbook (within currently authorized terminal operations)

### 1. Confirm the target and source

Read-only:

```bash
hostname -s
runuser -u semaphore -- id
runuser -u semaphore -- git -C /var/lib/semaphore/dh-agent-checkout rev-parse HEAD
runuser -u semaphore -- git -C /var/lib/semaphore/dh-agent-checkout status --porcelain
runuser -u semaphore -- /opt/ansible/venv/bin/ansible-inventory -i /var/lib/ansible/site-data/serzhan-home/hosts.yml --graph
ps -eo pid,user,args
```

Use the **public Git repository** `DigitalHouses/dh_ansible` and its exact intended commit. The independent checkout is deliberately separate from Semaphore's mutable `tmp/project_1/...` checkout, which can be stale and should not be modified during a Semaphore run.

Before any new code execution, when idle, update the independent checkout by ordinary `git fetch` under `semaphore`, pin the **exact reviewed full commit SHA**, and verify a clean working tree. Do not modify Semaphore-managed checkout or execute an unverified branch tip as a destructive deployment. The source of task-to-playbook mappings is `catalog/semaphore-tasks.json` **from the same checkout**.

### 2. Syntax-check a safe task

```bash
runuser -u semaphore -- /opt/ansible/venv/bin/ansible-playbook \
  --syntax-check \
  -i /var/lib/ansible/site-data/serzhan-home/hosts.yml \
  /var/lib/semaphore/dh-agent-checkout/playbooks/smoke-semaphore.yml
```

### 3. Execute a safe task and persist its native Ansible log

For **the previously reviewed read-only smoke playbook only**:

```bash
runuser -u semaphore -- env \
  ANSIBLE_LOG_PATH=/var/lib/semaphore/dh-agent-smoke-<unique-UTC-id>.log \
  ANSIBLE_ROLES_PATH=/var/lib/semaphore/dh-agent-checkout/roles \
  /opt/ansible/venv/bin/ansible-playbook \
  -i /var/lib/ansible/site-data/serzhan-home/hosts.yml \
  /var/lib/semaphore/dh-agent-checkout/playbooks/smoke-semaphore.yml
```

For any other catalog task, resolve its playbook and variables **from the current reviewed catalog**, check required credentials / survey and the expected side effects first. The command format is reusable; the authorization to execute a particular task is **not** implicit.

For longer tasks, retrieve output from the Remote Desktop Commander process session using its returned PID and `read_process_output`. Read the persisted log using **Remote Desktop Commander `read_file`**. Use a unique log path for each invocation; ensure file permissions remain `0600`. Do not transmit tokens/passwords in CLI argv or output; preserve the playbook's `no_log` for sensitive tasks. Native Ansible logs may still contain information requiring restricted file access.

### 4. Boundaries — critical

1. **Not all Semaphore tasks can be run directly.** As of 2026-11-03, `playbooks/plan-pve-media-vm.yml` (№80) requires `DH_GITHUB_PROVISION_TOKEN`, `DH_MQTT_PASSWORD` and a `DH_MQTT_USERNAME` environment value, normally provided by Semaphore native Variable Groups / secrets. These are **not automatically passed** to an out-of-band `ansible-playbook`. A direct №80 would fail or require a separately approved proper secret delivery mechanism. **Do not duplicate/decrypt/extract secrets from Semaphore's DB or token file to work around this.**
2. Direct runs do **not** produce entries in Semaphore UI task history, and this route cannot automatically retrieve historical Semaphore Task #18 raw output. Never report “Semaphore Task #NN Success” after a direct execution.
3. Semaphore's protected API-token access and certain DB introspection attempts were explicitly **denied by the connected tool**. Do not retry equivalent denied access via `curl`, code, alternate shell wrappers or privilege elevation. Use only legitimately available tool operations. A supported, separately authorized Semaphore API connector is a distinct future option; it is **not established today**.
4. Never equate general terminal access with blanket authorization to create/remove PVE guests or erase disks. Check live site/VM/controller/volume identity, parallel jobs, approved actions and secrets. Current media repair intent was `keep + import`, **not** `erase` or `recreate`. No task №80 deployment was performed in this direct-route acceptance.
5. The preinstalled standalone `scripts/dh-task-runner.py` described elsewhere is **optional, not part of this acceptance**, and should not be installed or used as a pretext for bypassing tool denials. No new Python, separate workers or per-task scripts are required to use the verified direct CLI path.

## Acceptance matrix

| Capability | Result |
| --- | --- |
| Remote terminal access on CT1231 | PASS |
| Run native Ansible as `semaphore` | PASS |
| Read site inventory, resolve current checkout | PASS |
| Native Ansible `--syntax-check` | PASS |
| Execute read-only smoke playbook | PASS |
| Save `ANSIBLE_LOG_PATH` as `semaphore`, permission 0600 | PASS |
| Agent independently reads full saved log | PASS |
| Inject native Semaphore credentials into direct CLI | NOT CONFIGURED |
| Start Task #NN using authenticated Semaphore API | NOT CONFIGURED |
| Read historical Semaphore UI task logs via this CLI | NOT CONFIGURED |
| Direct №80 full live deployment | NOT TESTED |

**Engineering interpretation:** autonomous execution and log reading from the ordinary chat are now demonstrated for self-contained, non-secret Ansible tasks. Full parity with Semaphore's native task execution and stored credentials requires a separately supported and authorized connection; do not misrepresent that gap as complete.
