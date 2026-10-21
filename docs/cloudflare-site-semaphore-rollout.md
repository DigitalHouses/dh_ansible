# Cloudflare · Semaphore onboarding for canonical site PVE

## Contract

- Canonical remotely managed Tunnel name: `<site-id>`, one per site.
- Canonical PVE hostname: `dh-<site-id>-pve.digitalhouses.vip`.
- PVE origin scheme, IP, port, tunnel ID, DNS record IDs, and authorized app
  identities are **object data**, not public code. Keep them in private Fleet.
- One new PVE route per approved action; never implicitly migrate unrelated
  routes, guests, or the site's older tunnel.
- Origin TLS trust must be verified. Never make `noTLSVerify` a fleet default.
- Existing encrypted site data is not a Semaphore Git repository.

## Scope of Task 10 in this stage

`10-Cloudflare · Туннель объекта (план)` performs **read-only**
observations of canonical site identity, PVE origin, public DNS,
unauthenticated HTTPS status for new and existing naming schemes, and
provides a plan with explicit markers for checks requiring a privileged
Cloudflare API worker.

Task 00 remains the only recovery/bootstrap template; Semaphore clones
public `dh_ansible` on each task invocation. The updated playbook can be
used without changing the immutable, accepted bootstrap installation pins.
Task 10 retains its Semaphore ID and history.

## Privileged apply design (not installed)

A future fixed-purpose root worker is needed, **not** arbitrary root execution
from unprivileged Semaphore:

1. One-time authorized provisioning of a narrowly scoped Cloudflare API token
   in a root-owned file (never in public Git, Semaphore environment, arguments,
   task output, or user-visible logs).
2. Worker accepts a fixed request containing only mode, exact site ID, and a
   nonce. Audit/plan before apply. Apply requires a separate, deliberate
   authorization and a revalidated current state.
3. Apply is idempotent and fails closed on unknown ingress, DNS, or Access
   collisions. Verify wildcard Access before publishing DNS and routing.
4. Connector cutover requires separate narrow PVE privilege; enabling this
   is **not implied** by the existing read-only PVE audit authorization.
5. Do not call a cutover successful until connector healthy, approved origin
   TLS trust, Access challenge, and post-authenticated PVE UI acceptance.

No client certificate, Cloudflare token, origin IP address or private
site inventory is permitted in this public document.
