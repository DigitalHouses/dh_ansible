# ADR — Semaphore-only bootstrap interface

**Status:** target design; pending end-to-end acceptance.

A site engineer uses one approved installer, then operates Ansible components through Semaphore. No parallel installation wizard is deployed.

- One codebase: `DigitalHouses/dh_ansible`.
- Site data and encrypted credentials: `DigitalHouses/dh_ansible_fleet_detail`, permanently private.
- GitHub authentication is only for owner-authorized acquisition of private data; public code is available without a deploy key.
- Different immutable source commits for code and Fleet Detail.
- Separate Semaphore Git repository records for public playbooks and private inventory.
- Safe read-only smoke/audit templates first; optional apply only after explicit review.
- Strong fail-closed gates: PVE identity, guest state, immutable Git refs, healthy system bus, verified Vault, and relevant site scope.

Legacy bootstrap remains unchanged until the new design passes full CI and E2E. Site-scoped Vault materialization remains an acceptance requirement.
