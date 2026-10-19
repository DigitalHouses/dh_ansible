# ADR 0017 · Public code / private Fleet Detail

**Status (2026-10-19):** public code repository has passed publication checks; private Fleet repository renamed. Full clean Bootstrap E2E **not accepted**. Sections below retain earlier migration decisions and historical staging guidance.

## Repositories

- `DigitalHouses/dh_ansible`: universal Ansible roles/playbooks/plugins, bootstrap, Semaphore seeder, CI, tests, generic examples and documentation. Intended PUBLIC **only after history-wide data and credential audit**.
- `DigitalHouses/dh_ansible_fleet_detail`: PRIVATE data-only repository. Target layout: `inventories/<site>/...` and one encrypted `secrets/<site>/vault.yml` per site. The existing `secrets/fleet.vault.yml` is legacy staging data until safely migrated; no implicit rename or deletion. No executable code, CI workflows or billed automation.

**One codebase. A home differs by data, not code.** No per-home branches, duplicate roles or per-home repositories.

## Canonical site identity and secrets passport (accepted target)

- The single canonical identifier is `site = <owner>-<type>`, e.g. `demo-office` or `sample-home`. Both parts are lowercase ASCII slugs; hyphens are separators, not underscores, spaces or display names. The owner slug may contain hyphens; do not recover the owner/type by splitting the full site ID. Inventory metadata will explicitly declare `site.owner_slug` and `site.type`, with invariant `site.id == site.owner_slug + "-" + site.type`.
- `<site>` MUST be identical in `site.id`, the inventory directory `inventories/<site>/`, the encrypted passport path `secrets/<site>/vault.yml`, Semaphore `--site`, and site-scoped runtime metadata. Display names (`site.name`) may be human-readable and must not be used as identifiers.
- Each site has **one** Ansible Vault-encrypted YAML secrets passport, internally grouped by component (`pve`, `truenas`, `cloudflare`, `backup`, etc.). Site-wide data stays declarative; splitting each component into a separate vault is not the default.
- In phase one, all site passports MAY use one operator-held Vault password. Separate Vault IDs/passwords per site are a future option: the file layout, site ID and consumer interface must not assume a globally shared password.
- Git access to Fleet Detail is repository-scoped: even separate per-site ciphertext files do **not** grant per-site GitHub authorization. Only owner-authenticated bootstrap on PVE may fetch private sources. Site runners receive site-only material; never GitHub credentials, the global master password, or other sites' ciphertext.
- Each site's canonical owner/type mapping comes from its PRIVATE inventory metadata and is verified before a rename. Example identifiers such as `demo-office` are synthetic, not claims that a site exists.
- Migration is an atomic audited change: inspect legacy Vault schema and all consumers, map/verify each secret without logging plaintext, prepare the new encrypted files, verify their decryptability and site isolation, update inventory IDs and dependent hostnames/CLI paths/tests, then cut over only after explicit acceptance. Preserve production, legacy git history and current live services throughout; do not rotate passwords or delete old paths prematurely.

The offline proof-and-stage tool and its explicit legacy-data gates are documented in [Fleet secret passport migration](../fleet-secret-passport-migration.md). Actual encrypted passport files have **not** been generated in GitHub; real source Vault decryption requires the operator password and the user-controlled private environment.

## Bootstrap contract (target)

1. Download an immutable, checksum-verified public code release without owner credentials.
2. Authenticate the owner to GitHub only to read the private Fleet Detail repository, pinned to an independently verified commit.
3. Verify encrypted Vault identity and expected site namespace before any guest write.
4. Keep complete Fleet Vault and operator credentials off ordinary site runners. Provision only site-scoped settings/secret material with root-only permissions, after owner confirmation.
5. Install the universal runtime on one site runner. Materialize only that site’s YAML inventory locally (root-owned and Semaphore-readable), and configure Semaphore file inventory to use its absolute path. Semaphore clones only public code. Only safe local templates may be seeded.
6. Fail closed on drift, unexpected files, missing owner authority, unhealthy D-Bus, and failed audits.

## Release gates

- Public repository MUST NOT contain Fleet Vault, encrypted or plaintext credentials, real site inventory, site-specific host keys/identifiers, private operational docs or credential-bearing test fixtures **anywhere in its history**.
- CI uses synthetic fixtures, not GitHub secrets or a checkout of private Fleet Detail.
- Semaphore inventory host-login credentials must be distinct from Fleet Detail Git deploy keys. Initial localhost-only smoke/audit templates use an unprivileged, generated dummy login; any real remote host access requires a separately reviewed credential.
- The Fleet Detail repository has no `.github/workflows`. GitHub Actions should additionally be disabled in its repository Settings.
- GitHub owner OAuth is transient on PVE. No Fleet Git deploy key, OAuth token, full Vault, or cross-site inventory is transferred to the runner. No CI credentials allow private Fleet Detail to be cloned.
- Local smoke/audit only until owner-mediated site-scoped secrets and remote access have an independently accepted contract.
- Production and the pilot CT1231 are not modified during this migration.
- The legacy private repositories and current installer remain live/unchanged until end-to-end cutover is accepted.

## Clean-root public cutover (not yet authorized)

1. Review the complete current source tree and scan all reachable Git refs
   while the candidate remains PRIVATE. The working tree scan is necessary
   but insufficient: deleted files are still reachable through ancestor commits.
2. Keep the existing private code history outside the future PUBLIC repository.
   The safest cutover is a fresh repository with a new initial root commit made
   from a reviewed source snapshot. Do not attach an old parent commit or carry
   across old branches, tags, pull-request refs or secret-bearing commit objects.
3. Independently check the fresh repository with
   `python3 bin/public-boundary-check.py` and
   `python3 bin/public-boundary-check.py --history`. Both must pass; a
   history failure is a BLOCK, not a reason to bypass the scanner.
4. Re-run CI, review generated release/checksum, and pin the **new** public
   code commit and approved PRIVATE Fleet Detail commit in the PUBLIC
   installer. Old pinned code SHAs will not work if the repository identity
   or commit graph is replaced.
5. Confirm the immutable installer, fresh Semaphore project and full Bootstrap
   E2E on the designated test site before accepting the rollout. Do not
   delete the original private repository or rebuild CT1231 without a separate
   explicit authorization.

Existing source history contains deleted site-operational documentation,
so the current code repository must **not** be switched to PUBLIC in-place.
Renaming, replacing or deleting repository identities is a separately
authorized administrative operation.

## Current limitations

The new code repository is still PRIVATE. Legacy code currently references the legacy private code repository and same-repository Fleet Vault and inventories. These references require refactoring before use. Not all existing tests have been migrated or adapted. Some historical documentation may contain private operational data. No publication or destructive cleanup is authorized by this ADR.
