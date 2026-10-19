# Semaphore bootstrap — release and acceptance runbook

**This is a target runbook, not permission to apply to an existing host.**

1. Verify that `dh_ansible` contains only public-safe code and documentation in **all history**, and that `dh_ansimble_fleet_detail` remains private with zero workflows.
2. Run public CI on synthetic inventory; run private site acceptance only under owner-controlled access. Do not connect private Git data to public Actions.
3. Build the release launcher manually, pinning both the reviewed code commit SHA and the independently reviewed private Fleet Detail commit SHA.
4. On the target PVE, perform read-only preflight before accepting a specific CT1231 action. The site inventory determines its `.231/24` address and other parameters.
5. Authenticate the owner for private Fleet Detail, verify encrypted Vault locally, and never transfer the fleet master password into guest logs.
6. On an approved fresh runner, install the universal runtime and verify systemd / D-Bus before proceeding. For a verified empty first-boot guest, use the explicitly approved non-destructive resume path.
7. Configure Semaphore: public HTTPS Git code repository with a None key; private read-only SSH Fleet Detail repository for inventory. Run safe smoke tests and audit, then record acceptance.
8. Only after full acceptance, retire old Git repositories and installer references.

This migration is **not yet approved for production**. Keep the old private repositories as rollback sources.
