# Native Semaphore credentials — №05, current-scope form candidate

**Status: CODE + ISOLATED ACTUAL SEMAPHORE 2.19.16 API E2E PASS.
USER-FACING TASK 05 UI E2E NOT YET ACCEPTED.**
Draft PR #101. Production catalog and secrets unchanged.

## Source-based scope of Task 05

Verified inventory: [site-credentials-inventory](../standards/site-credentials-inventory.md),
machine-readable [catalog/site-credentials.json](../../catalog/site-credentials.json).

One site form with exactly five optional fields:

1. GitHub repository provisioning token (used at initial site creation).
2. MQTT username.
3. MQTT password, reused by DH PVE Agent and DH Plex Agent.
4. **PVE root password**, stored as native Key Store `login_password` with fixed login=`root` (`root@pam` for PVE API). **No PVE API tokens in the form.**
5. Cloudflare API token, only with an authorized site-scoped
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

- Semaphore Variable Groups → Secrets for GitHub, MQTT password and Cloudflare (three secret values).
- The shared MQTT username is stored as a non-secret Variable Group env value.
- **Native Key Store → Login with Password** contains the entered **PVE root password**. Login is fixed at `root`, and replacing only this password uses `override_secret`.
- Native Key Store also holds generated/commissioned SSH identities (PVE, Git read-only deploy key); private keys do not need to be pasted into a Survey.
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
- Regression tests: semaphore catalog seeder, native credential enrollment,
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
  FIVE_FIELDS_CREATED=PASS
  ONLY_MQTT_PASSWORD_UPDATED=PASS
  ONLY_MQTT_USERNAME_UPDATED=PASS
  ONLY_PVE_ROOT_PASSWORD_UPDATED=PASS
  NATIVE_PVE_ROOT_KEY_STORE_VERIFIED=PASS
  NATIVE_METADATA_VERIFIED=PASS
  SQLITE_SECRETS_ENCRYPTED=PASS
  ISOLATED_TEST_RESOURCES_REMOVED=PASS
  PRODUCTION_SEMAPHORE_WRITES=NONE
  ```

- All test values were synthetic. No real credentials were read or created.

## Gates before merging

1. Confirm actual Semaphore UI Survey displays all 5 fields, accepts
   partial submission, and propagates Secret `target: env` without exposing
   values in Ansible stdout, task output, argv or re-run history.
2. Validate the site-specific catalog migration from legacy №05 without
   losing task ID/history or previously stored credentials. The current
   root-owned Seeder on the installed runner is pinned to an older code
   revision; **changing the public catalog alone does not deploy this Seeder**.
3. Validate consumer bindings: №06 must check PVE `root@pam` via password-based ticket authentication and distinguish this from SSH-key access. Legacy PVE API-token consumers must be migrated or explicitly blocked; №07 and Cloudflare tasks also need their native Semaphore bindings.
4. Implement №06 shared readiness check and lifecycle gates, as well as
   №07 private repository provisioning in the approved order.
5. Verify backup/restore of Semaphore SQLite plus
   `access_key_encryption`; smoke test after reboot.
6. Accept fresh e2e on `serzhan-lab`. Do **not** migrate or erase the
   working `ansible-serzhan-home` runner or change its real secrets.

**Never merge the Draft Task 05 catalog into production main or run №00
against it until these release gates are satisfied.**


## Revalidation and upstream compatibility review — 2026-11-02

Tested the exact candidate commit \`a1441b089c473af61bd46c639d31fd3e544e18bd\`, using a disposable checkout under \`/tmp\` on the **read-only production context** runner \`ansible-serzhan-home\`, with a separate loopback-only Semaphore 2.19.16 process, TLS certificate, project, admin and SQLite database. The live Semaphore project/database was neither read for values nor written to. The checkout and isolated resources were removed at completion.

- Standard-library unit tests for the native enrollment helper: **8/8 PASS**.
- New Task 05 template migration regression tests: **5/5 PASS** on current branch commit `94f07986883d0564fac2310b7ebf458f656c420a` (ID and task-history reference preserved; second apply unchanged; collisions/unmanaged survey/playbook drift rejected). These are in-memory seeder/API-stub tests, **not** a real production template migration.
- Standard-library unit tests for credential registry consistency: **4/4 PASS**.
- Real isolated Semaphore 2.19.16 REST API E2E: **PASS** (create five fields; replace only the MQTT password; replace only MQTT username; replace only PVE root password; preserve other entries; native Key Store metadata; encryption at rest; teardown). **Production Semaphore writes: none.**
- **Not tested:** browser-driven Task UI Survey submission, protected process environment/argv inspection during an actual Semaphore task, preservation of existing Task 05 template ID/history during a real migration, boot/recovery, real \`serzhan-lab\` commissioning and PVE \`root@pam\` ticket authentication. The only connected remote device was \`ansible-serzhan-home\`; do not mistake its identity for \`serzhan-lab\`.

### Source-proven capability and blocking distinction

Source reviewed at upstream **\`semaphoreui/semaphore@v2.19.16\` (\`f8cefe62\`)**:

1. \`services/tasks/local_executor.go\`: \`getSurveyEnvVars()\` passes Survey variables with \`target=env\` to the Ansible subprocess environment and \`getEnvironmentExtraVars()\` excludes those names from Ansible CLI \`--extra-vars\`. \`services/tasks/TaskPool.go\` clears the persisted task \`Secret\` before task storage, keeping the submitted secret separately only for execution. A **real browser/UI/argv/log E2E is still required**.
2. \`pkg/ssh/agent.go\` (\`KeyInstaller.Install\`) and \`services/tasks/local_executor.go\` (\`getPlaybookArgs\`) support a Key Store \`login_password\` for SSH inventory \`--ask-pass\`, Ansible \`become\` \`--ask-become-pass\` and Vault password. There is **no supported native binding that gives an arbitrary playbook a Key Store password as an environment variable for Proxmox HTTPS ticket authentication**.
3. \`api/projects/keys.go\` returns Key Store metadata, not decrypted passwords; \`db/AccessKey.go\` has \`json:"-"\` on the stored encrypted \`Secret\` field. The native REST API must not be treated as a plaintext secret reader.

References:
- https://github.com/semaphoreui/semaphore/blob/v2.19.16/services/tasks/local_executor.go
- https://github.com/semaphoreui/semaphore/blob/v2.19.16/services/tasks/TaskPool.go
- https://github.com/semaphoreui/semaphore/blob/v2.19.16/pkg/ssh/agent.go
- https://github.com/semaphoreui/semaphore/blob/v2.19.16/api/projects/keys.go

**Release blocker for Task 06:** The current owner-approved storage decision (PVE root password solely as a \`login_password\` Key Store entry) and the requirement to authenticate \`root@pam\` by HTTPS API ticket **cannot be connected through a supported generic Key Store-to-playbook secret input in OSS 2.19.16**. Do not claim this is implemented or bypass by reading/decrypting the Semaphore database or by creating a second untracked store. An approved architecture change could instead place the **one** user-entered PVE root password in **native Variable Groups → Secrets (type \`env\`)**, allowing Ansible to authenticate to Proxmox HTTPS, while Key Store would retain subsequently generated SSH keys; this would amend the specific field storage choice and requires owner approval before applying. A clean PVE may have a self-signed TLS certificate: the PVE HTTPS trust anchor/identity must also be commissioned and verified, not bypassed with \`validate_certs: false\`.

**Status:** Draft PR only. No merge, production Semaphore migration, target host changes or real credential modifications. This evidence does **not** constitute acceptance of Task 05 UI or Task 06.
