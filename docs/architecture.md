# DigitalHouses infrastructure as code

The project contains **one generic codebase**, not per-site implementations.

## Repository boundary

- `DigitalHouses/dh_ansible` — reusable Ansible roles, modules, playbooks, Semaphore bootstrap, synthetic tests, CI and public-safe documentation.
- `DigitalHouses/dh_ansible_fleet_detail` — **private**, data-only: `inventories/<site>/` and encrypted `secrets/fleet.vault.yml`. No executable source or GitHub Actions workflows.

A site differs by declarative data, **not code**.

## Runner / deployment

A site-local Debian LXC runner provides Semaphore and Ansible. Installers pin two immutable Git commits independently. Owner authorization is required only for private Fleet Detail. Semaphore uses a credential-free HTTPS repository for public code and a separate read-only SSH repository for site inventory. General playbooks use `inventory_dir`, while the CLI wrapper uses `DH_FLEET_DETAIL_DIR`.

No user credentials or site inventories may enter public CI logs, fixtures, artifacts or repository history. The encrypted Fleet Vault is *still sensitive*: it permits offline password guessing and does not in itself enforce site-scoped ciphertext access.

## Safety

Audit → plan → explicitly approved apply. No hidden guest recreation, mutable unpinned releases or production cutovers. Every site-specific operation must first verify site identity and declared scope. Home runner pilot acceptance precedes any production migration.

**Status:** private staging. Public visibility is forbidden until a clean Git history, complete secret scan, CI, and site E2E acceptance.
