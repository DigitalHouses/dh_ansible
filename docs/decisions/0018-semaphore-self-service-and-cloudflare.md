# ADR 0018 — Minimal Semaphore Bootstrap and remote management entrypoint

**Status:** Target architecture; implementation and fresh E2E pending.
**Decision date:** 2026-10-21.
**Scope:** All DigitalHouses site runners. Does not change the accepted CT1231 backup or the pinned Bootstrap artifact.

## Goal
A site differs by declarative Fleet data, not separate code. After a one-command
installer, the engineer has a local Semaphore UI with exactly one built-in task:
`10-Обновить список задач`. All other task definitions are loaded from the
public `DigitalHouses/dh_ansible` catalog on explicit invocation of task 10.
The private `DigitalHouses/dh_ansible_fleet_detail` repository remains
encrypted, private, and is **not** added as a Semaphore Git repository.

## Expected user workflow
1. Installer runs on the approved site PVE, creates a single local runner, sets up
   Semaphore, and seeds exactly the bootstrap task `10-Обновить список задач`.
   Installer must also supply its narrowly scoped root-owned synchronization
   worker, but must not install NetAlertX or automatically run infrastructure tasks.
2. Engineer opens local Semaphore and runs `10-Обновить список задач`.
3. Synchronizer validates source identity and desired manifest, presents an
   unambiguous plan (created / updated / unchanged / conflict), then applies only
   allowable catalog changes via localhost HTTPS Semaphore API. It never directly
   modifies Semaphore's database.
4. The synchronized catalog includes `00-Cloudflare · Доступ к Semaphore`,
   whose priority is enabling protected remote administration.
5. Engineer executes task 00 locally once; when protected remote connectivity
   is verified, subsequent jobs can be launched remotely through the site UI.

Task numbers describe UI ordering, **not** automatic execution dependencies.
Task 10 is initially the only built-in template; task 00 appears after
the first successful task 10 synchronization.

## Task 10 · Synchronize catalog
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
- A dedicated root-owned fixed-purpose worker uses root-only Semaphore
  credentials. Semaphore itself runs unprivileged and can submit only a
  validated fixed-action request; not a root shell, arbitrary playbook,
  file path, URL or command. Use a bounded timeout and atomic status response.
- Explicitly report catalog source commit SHA, revision, and per-task result.
  A failed update leaves the existing working catalog intact as far as possible.

## Task 00 · Cloudflare remote entrypoint
- Canonical managed hostname proposal:
  `<site-id>-semaphore.digitalhouses.vip`;
  `serzhan-home-semaphore.digitalhouses.vip` -> runner
  `https://192.168.11.231:3000/` at this pilot.
  Values must derive from Fleet/site inventory, not be hard-coded per site.
- Respect accepted fleet principle: one Cloudflare account, **one tunnel
  per site/PVE**, many published applications on that tunnel.
  Reuse the existing site's PVE connector/tunnel when present; no new
  tunnel per app. Creating the site's first tunnel/connector is a separate
  branch of the same task with explicit privileged provisioning.
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
  smoke task 10 without audited migration, and do not destroy its Golden
  backup. Install the new synchronizer once via a guarded site-only
  maintenance path; new fresh installers have only task 10.
- Existing PVE writes are disabled; task 00 must not silently escalate
  from read-only PVE access.
- New Bootstrap logic requires new pins, public CI, synthetic migration
  tests, and a subsequent full one-pass fresh E2E before acceptance.
  The currently accepted backup remains an older immutable release.

## Acceptance criteria
- Fresh installed Semaphore initially shows **only** task 10.
- Task 10 self-syncs all supported catalog entries, repeat run changes zero.
- A broken public catalog leaves task 10 and the working UI accessible.
- Task 00 refuses to publish a route without matching Access protection.
- Remote UI can be reached through CF Access without any inbound WAN port,
  and only after successful policy authorization.
- Read-only smoke audit of local runner and PVE remains available post-sync.
