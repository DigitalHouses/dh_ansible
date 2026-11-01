# Semaphore 2.20 Beta — Fresh runner pilot (02.11.2026)

## Decision and boundaries

Owner approved destroying and recreating the existing **single** Ansible runner container, without migration, credential copying or reuse of the old Semaphore database. Target from the current runbook: **PVE `192.168.11.6`, CT1231, `ansible-serzhan-home`, site `serzhan-home`**. Other guests, disks and VMs are explicitly out of scope.

This document records preparation, **not a claim that PVE was modified**.

- Upstream release: `v2.20.16-beta9` (pre-release, 2026-10-28).
- Official binary: `semaphore_2.20.16-beta9_linux_amd64.deb`.
- SHA256: `7e26db3b09b8e9fe5d8bd5c3956f3640dfc748b290f0b0b282746ba2633bf03a` (GitHub release API digest and independent temporary download).
- Pinned application code: `e27aa2df524aaf1b614d71c4cf1b7a2935f016f8`.
- Pinned installation launcher: `1f733203f35d79941eb32840e1dc3fac4146ca28`.
- PR: https://github.com/DigitalHouses/dh_ansible/pull/101 (draft; not merged).

## Verified

1. The beta Debian package downloaded successfully and passed pinned SHA256 verification.
2. The extracted **beta** binary reports `2.20.16-beta9`; its `migrate`, `users add`, and `server` entry points were checked without changing live software.
3. New bootstrap contract tests: 3/3 PASS; Ansible guest installer Python syntax and both shell entry points PASS.
4. Isolated beta Semaphore server, temporary TLS and SQLite: API login, five synthetic test credentials, selective update/KEEP, Key Store metadata, Variable Groups and encrypted SQLite payload: PASS. No production database writes. No real browser UI E2E.
5. The replacement script verifies actual PVE vmbr0 IP, exact CTID, hostname, rootfs, unprivileged state, network address and absence of external mounts before any destructive action. It pre-downloads the Debian 13 template. **Same-site fresh rebuild is allowed**.
6. The four-stage installer remains fresh-only and creates only Task 00. There are no data migration paths.

## Not yet performed

- Live PVE/CT1231 inventory and rootfs checks: NOT VERIFIED (connected Desktop Commander provides only the **guest** `ansible-serzhan-home`; `root@192.168.11.6` SSH was not authorized).
- Destruction of CT1231: NOT EXECUTED.
- New LXC creation, successful service start, HTTPS, Task 00 and browser E2E: NOT EXECUTED.
- No production Semaphore secrets or current guest files have been removed or changed.

## One command on the approved PVE root console

The URL and SHA are immutable. **The command is destructive for CT1231 after its guards pass**; it does not request a backup or migrate data.

```bash
curl -fsSL 'https://raw.githubusercontent.com/DigitalHouses/dh_ansible/1f733203f35d79941eb32840e1dc3fac4146ca28/bootstrap/replace-site-runner.sh' |
  bash -s -- serzhan-home serzhan-home 1f733203f35d79941eb32840e1dc3fac4146ca28 192.168.11.6
```

Expected new runner address `192.168.11.231/24` and Semaphore URL `https://192.168.11.231:3000/`. On failure, do not delete any additional containers blindly. After success, verify:

```bash
pct exec 1231 -- semaphore version
pct exec 1231 -- systemctl is-active digitalhouses-semaphore.service
pct exec 1231 -- cat /root/dh-semaphore-initial-admin-password
```

The last command displays a newly generated password only on the operator's PVE console. It is **not** an exported or migrated password. The old Remote Desktop Commander connection will disappear on recreation, and a new connection needs a later explicit setup. Task 00 starts manually via Semaphore.

**Acceptance remains PENDING** until live postconditions are independently observed.
