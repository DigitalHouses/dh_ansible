# ADR-0023 — Native Ansible checks instead of custom Python tests

**Status:** owner-directed, 2026-11-03. **Implementation:** draft PR #157, not approved for production.

## Decision

- Remove the repository's Python test files. Do not add new `tests/*.py` or `pytest` orchestration.
- Use native Ansible contracts (`assert`, `uri`, `command`, `--syntax-check`) and native shell validation for infrastructure changes. Preserve real live postcondition testing separately.
- Python used internally by Ansible and maintained collections remains permitted. Custom Python in `bin/`, `bootstrap/`, `plugins/`, `roles/`, or `scripts/` is NOT permitted to change without owner approval. Removing Python tests does not mean those existing Python executors have been converted.
- Keep CI mandatory and retain fail-closed repository boundary, pinned source verification, and TLS/SSH checks. Do not treat green syntax checks as proof of E2E safety.

## What changed in the draft branch

- Retired 134 legacy `tests/*.py` files and one separate `.ci/semaphore_api_live.py` live integration test.
- Replaced active `pytest` CI commands with native Ansible static contracts and syntax checks.
- Added native site credential/repository/Media80 contract checks and basic HTTPS/authenticated Semaphore live API checks.
- Package/install scripts and guest application Python remain untouched.

## Missing coverage that MUST NOT be misrepresented

Legacy `pytest` contained 800+ tests spanning credential lifecycle, MQTT/Cloudflare token validation, Semaphore migration and template ID preservation, Samba/Filestash/Jellyfin/Plex account flows, recovery, ACL, cryptographic and public-source boundaries. The new native Ansible contracts test only a subset. Deleting Python test files alone does not create equivalent behavior coverage.

Production acceptance remains **BLOCKED** until native Ansible live tests or other agreed maintained upstream tools cover the essential behaviors, including clean/bootstrap/repeat, site identity, private repo, PVE SSH/TLS, secret handling and all six Media80 action combinations. Do not turn off CI, bypass job failures or declare PASS based solely on a static contract.

## Python runtime audit

At the time of the draft after test cleanup, 83 own Python source files remained: 8 in `bin`, 28 in `bootstrap`, 19 in `plugins`, 20 in `roles`, 8 in `scripts`. These are NOT tests. Some are application runtime or genuine Ansible custom modules; others still provide workflow orchestration. A separate owner-approved inventory and gradual replacement is needed. The removal of tests does not grant authority to rewrite them.
