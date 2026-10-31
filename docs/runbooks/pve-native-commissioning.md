# PVE native commissioning

One-time infrastructure enrollment; not a task-specific worker.

Run on the actual Proxmox VE host as root, using a pinned copy of
`bootstrap/commission-pve-runner.py` from `DigitalHouses/dh_ansible`.
Command: `python3 /tmp/dh-commission-pve-runner.py --runner-ctid 1231`.

## Actions

- Validate the real PVE `vmbr0` IPv4, the existing site runner CT, and PVE TLS identity.
- Generate one ED25519 keypair owned by `semaphore` inside the site runner.
- Append only its public key to PVE root authorized_keys, preserving existing keys.
- Pin the PVE SSH host key and current TLS certificate in local runner runtime data.
- Save read-only disk, PCI, ZFS, guest and PVE storage observations locally.
- Verify noninteractive SSH as the `semaphore` user; no fleet secrets or passwords are copied.

The live PVE address is obtained from the host, never computed from VMID,
hardcoded per site or written as an exception to Git.

The script does NOT create or destroy VMs, modify disks, create ZFS pools,
migrate anything or modify other guests. Repeated use reuses the same SSH key.
On failure it reports `PVE_COMMISSIONING=BLOCKED`.

## Acceptance

Expected: `PVE_NATIVE_SSH=PASS`, `PVE_HARDWARE_AUDIT=PASS`,
`PVE_COMMISSIONING=PASS`. Then run `playbooks/audit-pve-native.yml`
using normal Ansible execution from CT1231. Task 50 in the catalog uses
this native read-only audit after refreshing Task 00.

## Privilege boundary

Root SSH on one PVE host is powerful and is trusted only because the
site runner is controlled by the owner. Protect the runner and its private key.
The key is not available to other sites. Destructive media/disk operations
still require exact device identification and separate validation.

## New-server state (2026-10-31)

Runner CT1231: administrative commissioning PASS.
Actual PVE HTTPS endpoint is reachable, but its TLS fingerprint differs
from historical Fleet metadata. Old protected PVE worker is not installed.
The old media VM Fleet configuration retains the old disk serial/GUID and
Intel GPU/SATA addresses. It must not be applied to new hardware without
the read-only hardware report. Media deploy Task 80 and acceptance 81
still use legacy worker code and are NOT yet migrated or accepted.
