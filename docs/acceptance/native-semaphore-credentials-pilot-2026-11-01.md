# Native Semaphore credentials — pilot 05 (2026-11-01)

**Status: CODE + ISOLATED API E2E PASS; LIVE TASK 05 IN SEMAPHORE NOT YET ACCEPTED.**

## Scope

A staging branch implements `05-Ввести credentials объекта` with **two
explicitly dummy-only optional Secret Survey fields**:

- `DH_TEST_KEYSTORE_PASSWORD` → Key Store login_password,
  `DH TEST ONLY - Login Password`;
- `DH_TEST_VARIABLE_SECRET` → Variable Groups Secrets (`env`),
  `DH TEST ONLY - Secret Variables`.

Both fields have `target: env` in Semaphore 2.19.16 to keep submitted
passwords out of Ansible `--extra-vars` argv. Blank field = KEEP;
updates go through official `override_secret` / environment
`secrets[].operation: update`. The Python helper uses only the
existing localhost Semaphore API, already-pinned TLS CA and the
root-owned API token from the accepted Bootstrap. It runs under
ordinary Ansible `become` within the same site runner LXC.
It neither creates a new credential store nor touches PVE.

## Verified on authorized runner (read-only for the live Semaphore)

- Runner: `ansible-serzhan-home`, Semaphore binary `2.19.16`.
- Live API read-only probe: site project validated; Key Store and Variable
  Groups list operations PASS; **zero live writes**.
- `python3 -m unittest ... test_semaphore_native_obtain_pilot.py`: 7 PASS.
- `pytest ... test_semaphore_project.py test_semaphore_native_obtain_pilot.py`:
  **29 PASS** in temporary test-only virtual environment.
- Python compilation: PASS.
- `ansible-playbook --syntax-check`: PASS (initial host-pattern warning from
  intentionally minimal CLI inventory, not a playbook validation error).

## Actual Semaphore 2.19.16 integration

Ran `python3 tests/e2e_semaphore_native_pilot.py` on runner. The test
created an ephemeral **loopback-only** Semaphore service with its own
temporary TLS certificate, admin account, API session, and SQLite DB.
It exercised the same public Python API helper, with real Semaphore
API endpoints:

- `ISOLATED_API_LOGIN=PASS`
- `BOTH_NATIVE_SECRETS_CREATED=PASS`
- `PARTIAL_UPDATE_KEEP_KEY_STORE=PASS`
- `PARTIAL_UPDATE_KEEP_VARIABLE_GROUP=PASS`
- `NATIVE_METADATA_VERIFIED=PASS`
- `SQLITE_SECRETS_ENCRYPTED=PASS` (no literal dummy values in SQLite)
- `ISOLATED_TEST_RESOURCES_REMOVED=PASS`
- `PRODUCTION_SEMAPHORE_WRITES=NONE`

## Release gate (still open)

- Validate actual Semaphore 2.19.16 Secret Survey form and
  `target: env` delivery **from the task UI** without logging secrets.
- Verify `00` catalog migration preserves existing Task 05 ID/history,
  and that old accepted installations do not lose their working credentials.
- Expand the schema beyond two **dummy-only** values to the approved
  real site credentials, without custom storage.
- Build `06-Check Credentials` using native bindings; verify ALL required
  credentials and show an actionable aggregate failure report.
- Confirm encrypted backup/restore of the native Semaphore DB and its
  `access_key_encryption` key.
- Perform end-to-end acceptance on the intended new site `serzhan-lab`.
  The old site runner is **not** that site; do not use its credentials or
  migrate its project by attempting these tests.

**Never merge the pilot catalog into production `main` or trigger
existing Task 00 against it without the documented gate.**
