# Native Semaphore credentials — №05, current-scope form candidate

**Status: CODE + ISOLATED ACTUAL SEMAPHORE 2.19.16 API E2E PASS.
USER-FACING TASK 05 UI E2E NOT YET ACCEPTED.**
Draft PR #101. Production catalog and secrets unchanged.

## Source-based scope of Task 05

Verified inventory: [site-credentials-inventory](../standards/site-credentials-inventory.md),
machine-readable [catalog/site-credentials.json](../../catalog/site-credentials.json).

One site form with exactly six optional fields:

1. GitHub repository provisioning token (used at initial site creation).
2. MQTT username.
3. MQTT password, reused by DH PVE Agent and DH Plex Agent.
4. PVE API audit token, only if the legacy API workflow is used.
5. PVE API write token, only if the legacy API workflow is used.
6. Cloudflare API token, only with an authorized site-scoped
   Cloudflare integration; the current Cloudflare workers still need
   separate consumer migration.

No manual Plex agent API token, media application passwords, Radarr/Sonarr/
Prowlarr API keys, PVE SSH private key, NUT PRIMARY password or Home Assistant
API token is required by the confirmed current published task catalog.
These values are either generated/commissioned by their own system,
auto-provided by HAOS, or genuinely deferred until a task needs them.

**Important:** The form contract is candidate v1; no actual production
credentials should be submitted until the protected UI secret path has
passed acceptance.

## Native storage

- Semaphore Variable Groups → Secrets for five sensitive API/MQTT values.
- The shared MQTT username is stored as a non-secret Variable Group env
  value in the same group.
- The separate Key Store remains the native home for generated/commissioned
  SSH identities (PVE, Git read-only deploy key); do not ask owners to
  paste private keys into task Survey fields.
- Secret Survey inputs use `target: env` on Semaphore 2.19.16,
  not Ansible `--extra-vars` CLI arguments.
- Task 05 uses the official localhost API, accepted runner-local
  revocable API token, TLS CA, and ordinary `become` inside its own LXC.
  No parallel credential store.
- Empty field = KEEP. Only provided credentials are created/updated.
  The updated secret value is never returned or logged.

## Tested on the authorized runner

- Runner name `ansible-serzhan-home`, Semaphore 2.19.16.
  Read-only live API probe passed; **no live API writes**.
- 35 pytest tests: semaphore catalog seeder, native credential enrollment,
  inventory consistency, task design standard — PASS.
- Python compile and Ansible playbook syntax check — PASS.
  The syntax-only command used a deliberately minimal CLI inventory and
  emitted a nonfatal host-pattern warning.
- `python3 tests/e2e_semaphore_native_pilot.py` started a **temporary
  loopback-only Semaphore** with independent TLS certificate, admin account
  and SQLite database, and exercised the production candidate helper against
  the real upstream API:

  ```text
  ISOLATED_API_LOGIN=PASS
  SIX_FIELDS_CREATED=PASS
  ONLY_MQTT_PASSWORD_UPDATED=PASS
  ONLY_MQTT_USERNAME_UPDATED=PASS
  NATIVE_METADATA_VERIFIED=PASS
  SQLITE_SECRETS_ENCRYPTED=PASS
  ISOLATED_TEST_RESOURCES_REMOVED=PASS
  PRODUCTION_SEMAPHORE_WRITES=NONE
  ```

- All test values were synthetic. No real credentials were read or created.

## Gates before merging

1. Confirm actual Semaphore UI Survey displays all 6 fields, accepts
   partial submission, and propagates Secret `target: env` without exposing
   values in Ansible stdout, task output, argv or re-run history.
2. Validate the site-specific catalog migration from legacy №05 without
   losing task ID/history or previously stored credentials. The current
   root-owned Seeder on the installed runner is pinned to an older code
   revision; **changing the public catalog alone does not deploy this Seeder**.
3. Validate consumer bindings: future №06 and №07 templates must attach the
   native site credential group correctly. Existing PVE API and Cloudflare
   workers currently use their prior secret paths and **do not** automatically
   consume native Semaphore values.
4. Implement №06 shared readiness check and lifecycle gates, as well as
   №07 private repository provisioning in the approved order.
5. Verify backup/restore of Semaphore SQLite plus
   `access_key_encryption`; smoke test after reboot.
6. Accept fresh e2e on `serzhan-lab`. Do **not** migrate or erase the
   working `ansible-serzhan-home` runner or change its real secrets.

**Never merge the Draft Task 05 catalog into production main or run №00
against it until these release gates are satisfied.**
