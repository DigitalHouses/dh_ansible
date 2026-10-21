# Semaphore task 10 — Создание туннеля на PVE

## User-facing contract

The Semaphore task catalog contains **one** Cloudflare lifecycle entry:
\`10-Создание туннеля на PVE\`.

- \`audit\`: verify local site and PVE identities and Cloudflare API token
  kept root-only **on PVE**; verify remotely-managed tunnel, ingress, DNS,
  wildcard Access application and attached administrator policy.
- \`plan\`: determine \`INSTALL_NEW\`, \`RECONFIGURE_EXISTING\` or
  \`VERIFY_ONLY\` from the actual PVE systemd and connector state.
- \`apply\`: if missing, install \`cloudflared\` from Cloudflare's signed
  Debian APT repository and create exactly one systemd service. If present,
  reuse the same service, apply a scoped drop-in and a root-only token file.
  No new process/service if an existing \`cloudflared\` is running.
- \`verify\`: require Cloudflare API tunnel \`healthy\` and local service
  active. Repeated execution must report \`CF_CUTOVER=ALREADY_APPLIED\`
  and \`CLOUDFLARE_APPLY=UNCHANGED\`.
- On failure, remove only the newly staged unit/drop-in/token and attempt
  to restore the prior systemd state. An APT-installed package may remain.
- Browser Access post-authenticated check is **manual**; Semaphore must not
  be granted an Access bypass or browser account.

### Configuration prerequisites

Cloudflare account/zone identity, site PVE origin, remotely managed Tunnel
ID, DNS and ingress route, and administrator Access policy must be provisioned
or reconciled by authorized infrastructure onboarding before this task.
One-time \`--enroll\` securely stores the narrowly scoped API token and
validated site metadata on PVE; it accepts a site without a legacy tunnel.

This stage does **not** create the Cloudflare account, new remote Tunnel
object, DNS record or Access policy. It configures the connector process on
PVE after those server-side prerequisites pass. Absent prerequisites must
fail closed, never publish an unprotected public route.

Site-specific observed routes and ports are in the PRIVATE
\`dh_ansible_fleet_detail\` inventory and are not copied into public code.

### Existing home-site upgrade

The accepted host and runner previously used manual task 11. Upgrading to
the one-task lifecycle requires a pinned, root-authorized replacement of:

1. PVE fixed-purpose host worker \`dh-cloudflare-pve-cutover\`.
2. Runner pinned \`semaphore-project.py\` (task 00 catalog sync code).
3. Runner fixed-purpose worker if its timed installation contract is changed.
4. Systemd runner unit timeout.

\`bootstrap/cloudflare-task10-upgrade.py\` checks exact historical trusted
source bytes against the previous accepted commit before replacing the
two privileged Python files. It **does not** change the active connector,
rotate tokens, or edit the Semaphore DB.

The fixed-purpose runner worker and associated timeout should be upgraded
separately before fresh-install acceptance. The existing home site has an
active and accepted Tunnel and does not require a fresh install.

### Task 11 retirement and history

After updating the pinned Semaphore synchronizer, task \`00\`:

1. Confirms the exact legacy title, managed ownership and bound playbook.
2. Updates task \`10\` **in place** from its old name/playbook to the canonical
   one, preserving the template ID and its old task history.
3. Enumerates all current project tasks (up to 199, otherwise fail closed)
   and exports the old task-11 metadata and raw output API records to
   root-only \`/root/dh-semaphore-template-history/\`.
4. Verifies the archive and only then removes the redundant task-11
   **template** through the supported Semaphore REST API.
5. Verifies only one canonical Cloudflare template remains. Prior task logs
   are preserved in the root archive; whether Semaphore retains the linked
   UI history after template deletion is not assumed.

No direct SQLite changes or unconditional template deletes.

### Limitations

- Cloudflare SaaS provisioning and publishing all other service routes from
  Fleet inventory are separate future enhancements **inside the same task
  10 lifecycle**, not new numbered tasks.
- This initial clean-PVE install path is covered by unit/CI checks but still
  requires a destructive fresh-PVE E2E test before calling it accepted.
- Root-only API token and explicit onboarding remain necessary. No system
  can provision a Cloudflare account without authorized credentials.
