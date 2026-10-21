# ADR 0018 — Minimal Semaphore Bootstrap and remote management entrypoint

**Status:** Target architecture; implementation and fresh E2E pending.
**Decision date:** 2026-10-21.
**Scope:** All DigitalHouses site runners. Does not change the accepted CT1231 backup or the pinned Bootstrap artifact.

## Goal
A site differs by declarative Fleet data, not separate code. After a one-command
installer, the engineer has a local Semaphore UI with exactly one built-in task:
`00-Обновить список задач`. All other task definitions are loaded from the
public `DigitalHouses/dh_ansible` catalog on explicit invocation of task 00.
The private `DigitalHouses/dh_ansible_fleet_detail` repository remains
encrypted, private, and is **not** added as a Semaphore Git repository.

## Expected user workflow
1. Installer runs on the approved site PVE, creates a single local runner, sets up
   Semaphore, and seeds exactly the bootstrap task `00-Обновить список задач`.
   Installer must also supply its narrowly scoped root-owned synchronization
   worker, but must not install NetAlertX or automatically run infrastructure tasks.
2. Engineer opens local Semaphore and runs `00-Обновить список задач`.
3. Synchronizer validates source identity and desired manifest, presents an
   unambiguous plan (created / updated / unchanged / conflict), then applies only
   allowable catalog changes via localhost HTTPS Semaphore API. It never directly
   modifies Semaphore's database.
4. The synchronized catalog includes `10-Cloudflare · Туннель объекта (план)`,
   whose priority is protected remote administration of PVE and its guests.
5. Engineer runs task 10 (Cloudflare) after task 00 has populated the catalog.
   As of this revision task 10 is PLAN_ONLY; provisioning and the verified
   Access/Tunnel remote entrypoint remain a separate implementation milestone.

Task numbers describe UI ordering, **not** automatic execution dependencies.
Task 00 is initially the only built-in template; task 10 (Cloudflare planning)
appears after the first successful task 00 synchronization.

## Task 00 · Synchronize catalog
- Public code repository `dh_ansible` is authoritative for catalog definitions.
  Semaphore already clones public repository playbooks at execution time;
  synchronization specifically manages template metadata, views, tasks,
  and approved bindings.
- Keep a single local runner inventory, explicit site ID, no site secrets in
  logs, no Fleet Git credentials on the guest.
- The self-synchronization task must remain recoverable even if public
  catalog is malformed or a network request fails; never remove the only
  bootstrap task.
- Idempotent comparison: CREATE missing; UPDATE known owned templates in
  place preserving IDs and execution history; report UNCHANGED; BLOCK unknown
  collisions/drift. No automatic delete or autorun.
- A dedicated root-owned fixed-purpose worker uses a revocable Semaphore API
  token stored in a root-only file (0600), independent of the UI password. Semaphore itself runs unprivileged and can submit only a
  validated fixed-action request; not a root shell, arbitrary playbook,
  file path, URL or command. Use a bounded timeout and atomic status response.
- Explicitly report catalog source commit SHA, revision, and per-task result.
  A failed update leaves the existing working catalog intact as far as possible.

## Task 10 · Cloudflare remote entrypoint
- Canonical **tunnel name = `<site-id>`**, not an individual app or guest.
  Home pilot: Cloudflare Tunnel `serzhan-home`. A single connector on the
  site's PVE routes traffic to its PVE UI and its VM/LXC services.
- Canonical public hostnames under `digitalhouses.vip`:
  - PVE itself: `<site-id>-pve.digitalhouses.vip`.
  - Service hosted in a PVE guest: `<site-id>-pve-<guest-name>.digitalhouses.vip`.
  The `guest-name` is the unique declarative service/guest slug, **not**
  necessarily the actual Linux hostname or the numeric VMID/CTID.
- Accepted concrete mappings for `serzhan-home`:
  - `serzhan-home-pve.digitalhouses.vip` =>
    `https://<site-internal-ip>:8006` (PVE).
  - `serzhan-home-pve-ansible.digitalhouses.vip` =>
    `https://<site-internal-ip>:3000` (CT1231 Semaphore).
  URL route and origin scheme must be treated as separate data fields.
- Values must derive from canonical Fleet/site inventory; validate uniqueness
  of guest/service slugs on the site, not hard-code mappings per site.
- Respect the fleet principle: one Cloudflare account, **one tunnel per site**,
  many published applications on that tunnel; do not create a tunnel per guest.
  Reuse the existing site's PVE connector/tunnel if present; create the first
  tunnel/connector only after explicit privileged authorization.
- First verify or create Cloudflare Access protection for the exact hostname,
  using approved DigitalHouses administrator policy. Fail closed unless
  protection is verified; only then publish DNS and tunnel route. Prefer
  require-Access-by-default when compatible with the entire zone.
- Verify local origin reachable from connector; retain TLS verification
  using an approved origin CA/hostname mechanism (avoid permanently using
  `noTLSVerify`). Tunnel healthy alone is not remote UI acceptance.
- Verify final URL yields Cloudflare Access for unauthenticated requests,
  then the Semaphore sign-in UI after administrator authentication.
  Report remote URL and health. Never print Cloudflare management secrets.
- A public Git repository, GitHub OAuth login, or an existing read-only
  PVE audit grant **does not** authorize creating CF resources or changing
  the PVE host. Task 00 must have an explicit owner-authorized least-privilege
  Cloudflare token and narrowly scoped site provisioner. No account-wide
  `cert.pem` or CF admin token on Semaphore.
- Keep Cloudflare tokens only in root-only ephemeral or scoped local
  store; the runner gets neither Fleet master password nor private Git key.

## Compatibility / migration
- Existing restored CT1231 has templates 10/20/30/40; migrate
  deliberately, preserving IDs and history. Do not overwrite the current
  smoke task 00 without audited migration, and do not destroy its Golden
  backup. Install the new synchronizer once via a guarded site-only
  maintenance path; new fresh installers have only task 00.
- Existing PVE writes are disabled; task 00 must not silently escalate
  from read-only PVE access.
- New Bootstrap logic requires new pins, public CI, synthetic migration
  tests, and a subsequent full one-pass fresh E2E before acceptance.
  The currently accepted backup remains an older immutable release.

## Acceptance criteria
- Fresh installed Semaphore initially shows **only** task 00.
- Task 00 self-syncs all supported catalog entries, repeat run changes zero.
- A broken public catalog leaves task 00 and the working UI accessible.
- Task 00 refuses to publish a route without matching Access protection.
- Remote UI can be reached through CF Access without any inbound WAN port,
  and only after successful policy authorization.
- Read-only smoke audit of local runner and PVE remains available post-sync.

## Implementation status (2026-10-21)

- Bootstrap seeder now defines only `00-Обновить список задач`.
- A narrow root-owned systemd.path worker performs catalog sync via the
  existing localhost HTTPS Semaphore API.
- Public catalog `catalog/semaphore-tasks.json` declares the rest;
  invalid manifests block without deleting existing templates.
- The initial Cloudflare task `10-Cloudflare · Туннель объекта (план)` is
  **read-only planning only**. It does not create Cloudflare accounts, tokens,
  Access policies, routes, or tunnels. A separately authorized provisioner
  and runtime acceptance are still required.
- This implementation is not yet production accepted: public CI and fresh
  destructive E2E must pass, and the immutable installer pin must be advanced.
- Do not assume a restored older CT includes the sync worker or initial 00 task.

## Pending task-10 lifecycle consolidation (2026-10-21)

**Accepted target name:** `10-Создание туннеля на PVE`. The prior
`10-Cloudflare · Туннель объекта (план)` must be renamed **in place**,
preserving Semaphore template ID and task history. An explicit catalog alias
migration handles only that exact known managed template; it blocks collisions
and unexpected playbook drift.

The final task executes one declarative **audit → plan → apply → verify**
operation on the PVE host: install cloudflared if absent, reconfigure the
existing service if present, no action when in the desired state, and
rollback on a failed connector cutover. Only one connector service and one
canonical site tunnel; no extra UI tasks for separate lifecycle phases.

**Activation gate:** Do not publish the new display name to the live catalog
until task 10 implements this behavior and the pinned on-runner catalog
synchronizer is updated. The current fixed root synchronizer does not
automatically update its own code from GitHub. The old task 11 must be
retired through a separate controlled history-preserving migration;
removing its manifest entry alone is not sufficient.

## Catalog task 10 activation

The canonical title is `10-Создание туннеля на PVE`. Template 10 uses the
fixed-purpose cutover worker. Task 11 is removed from the *desired catalog*,
but will remain in older Semaphore databases until its execution logs are
safely archived; removing the template through Semaphore 2.19.16 API
cascades to task records and output. Do not delete 11 without a verified
out-of-band backup of the task history. Fresh installs expose only task 10.
