# Remote Desktop Commander on Ansimble site runner

**Standard component**: `playbooks/apply-runner-remote-commander.yml` using the existing `runner_remote_commander` role.

- Runs **locally** in the target Ansimble LXC as `root`. Never install Git, Node.js or Desktop Commander on the PVE host. The playbook refuses non-local inventories and non-`ansimble-*` hosts.
- Does not create any system users. The systemd service runs as `root` within the isolated LXC and retains device pairing under `/root/.desktop-commander-device`.
- Installs pinned Node.js 24.22.0 (official SHA256), pinned `@wonderwhy-er/desktop-commander@0.2.52`, and `digitalhouses-remote-commander.service`. Re-running is idempotent.
- Service uses `NODE_OPTIONS=--dns-result-order=ipv4first --network-family-autoselection-attempt-timeout=2000`, verified in the home CT1231 to avoid the observed Node.js `ETIMEDOUT` issue.
- The first device registration remains the official one-time browser pairing and must be performed by the owner; neither GitHub nor the playbook carries the pairing secret.
- If a **foreground** `desktop-commander remote` instance is running, the playbook blocks before package or service operations. After initial pairing, stop that foreground session with Ctrl+C, then let systemd take ownership. It does not stop running terminal sessions automatically.
- On a fresh, unpaired runner, the playbook starts the service. Open its **local** journal to obtain the one-time device verification link/code; complete pairing in your browser. Never paste pairing links, codes or account details into issue trackers or chat.

## Usage

Use an Ansimble LXC with the standard Ansible virtualenv installed and the universal `dh_ansible` checkout available. Run from its repository root. Example local inventory is synthetic; real homes use the single site's verified private inventory.

```bash
clear
/opt/ansimble/venv/bin/ansible-playbook \
  -i /var/lib/ansimble/site-data/<site>/hosts.yml \
  playbooks/apply-runner-remote-commander.yml
```

For a **fresh** runner, first run the playbook, then inspect the new service's local journal for the pairing instructions:

```bash
clear
systemctl is-active digitalhouses-remote-commander.service
journalctl -u digitalhouses-remote-commander.service -n 35 --no-pager
```

Complete verification in the browser and confirm the device becomes Online. Do not share the pairing code or whole journal output.

For a **previously paired** runner (such as the home pilot), stop the existing foreground `desktop-commander remote` session first; the role deliberately detects and blocks duplicate interactive sessions. Then run the playbook. Because systemd uses the same user `root` and `HOME=/root`, existing device registration is reused.

If Ansible is not yet installed, the generic runner bootstrap must finish first; this playbook is not a replacement for the bootstrap installation.

Before applying: ensure the target LXC actually has Ansible and a verified `<site>` inventory. Do not copy production credentials to other homes. Only the current site runner is targeted.

Read-only acceptance: `systemctl is-enabled digitalhouses-remote-commander.service`, `systemctl is-active digitalhouses-remote-commander.service`, and the device's connection status in Desktop Commander. These service checks alone do not prove the cloud device is authenticated.

**Migration note:** Prior `site_runner.remote_commander` settings in old Fleet Detail are legacy and not required by the new universal role. Do not apply the new playbook to Amir production without separate authority.


## Standard initial runner bootstrap

The pinned single-entry Semaphore installer (`bootstrap/semaphore-host-install.py`) now
downloads the Remote Commander playbook and its existing role from the same
verified `dh_ansible` code commit. After installing Semaphore and the selected
site's local inventory, it stages only those code files into
`/opt/digitalhouses/bootstrap/remote-commander` **inside CT1231** and runs
the original Ansible playbook with `ANSIBLE_ROLES_PATH` pointing at its staged role.

The service is enabled automatically; the user completes the first one-time
browser pairing from the service journal, not through another setup wizard.
Ansible and Node/npm/Remote Commander are installed **only within the runner
LXC**, never as PVE host services. PVE only uses `pct push` and `pct exec`.

The normal bootstrap refuses any existing CT1231; this integration is for
future installs/resume flows under the existing strict gate, **not** permission
to recreate or replace an already-accepted runner.
