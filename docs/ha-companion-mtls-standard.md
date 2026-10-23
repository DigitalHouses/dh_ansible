# HA Companion · Cloudflare mTLS · DigitalHouses

## Accepted fleet decisions (2026-10-23)

- **One certificate per household/site**, shared by its permitted iPhone/iPad devices. No per-device identity in this policy.
- Canonical **new route hostname**: `<owner-object>-pve-ha-mtls.digitalhouses.vip`,
  for example `serzhan-home-pve-ha-mtls.digitalhouses.vip`.
  The `pve` segment indicates the PVE site's VM/LXC service namespace, not
  an additional Cloudflare Tunnel: **one tunnel per site, multiple hostnames**.
- Legacy `amir-office-ha-mtls.digitalhouses.vip` is retained unmodified.
  `amir-office-pve-ha-mtls.digitalhouses.vip` is the canonical name for any
  **future approved** cutover. Do not automatically issue a second household
  identity or change the existing mobile Companion URL/certificate in Amir Office.
  A name migration needs a separately reviewed certificate-replacement plan.
- **One Semaphore catalog task №70**: `70-Cloudflare · HAOS · mTLS · Настроить`.
  Its existing playbook performs a read-only site preflight and, only when
  `haos.cloudflare.approved_for_publish: true`, requests a fixed-purpose
  privileged worker. There must be **no №71**.
- Reuse the site's existing canonical Cloudflare Tunnel. Never delete legacy
  tunnels/routes or modify PVE access to enable HAOS.

## Protection order

1. Verify site ID, local runner, PVE identity, HAOS VM and private origin.
2. Confirm or issue **one** 365-day Cloudflare Managed CA certificate
   using an RSA-2048 private key and CSR **already recovered from the
   merged encrypted Fleet Vault**. Task 70 must never generate a new
   private key, CSR or PKCS#12 password on PVE. Do not overwrite/revoke
   previous keys.
3. Preserve existing Managed CA hostname associations, adding just this host.
4. Verify/establish a global **block** WAF rule for `*-ha-mtls.digitalhouses.vip` (the `-pve-ha-mtls` suffix is included).
   Require `cert_verified`, **not** `cert_revoked`, and subject
   `CN=<requested http.host>`; reject skip rules preceding the guard.
   Adopt the existing Cloudflare production expression (verified GET 2026-10-23):
   `http.host wildcard "*-ha-mtls.digitalhouses.vip"` with
   `cert_subject_dn_rfc2253` prefix `CN=` and exact
   `substring(..., 3) ne lower(http.host)` rejection. Do not replace the
   active Amir rule with a syntactically different expression.
5. Verify/create only an **exact-host** Access application with `Bypass /
   Everyone`. Existing wildcard administrator Access stays untouched.
6. Only after steps 1–5: append one HAOS ingress rule before the existing
   tunnel's catch-all; preserve all other ingress entries.
7. Add one proxied CNAME pointed to the pre-existing tunnel.
8. End-to-end check: anonymous `/api/` **403**, with valid certificate
   `/api/` **401** (HA login is independent of the mTLS gate).
9. Re-run exactly the same task: root worker says `UNCHANGED`, Ansible
   `changed=0`; no new certificate, no extra DNS, WAF or Access objects.

Do not equate an ordinary HTTP 403 with evidence of WAF enforcement: Cloudflare
Access can also produce 403. The privileged worker validates the **actual**
Cloudflare WAF expression and policy readback before publishing any route.

## Privilege separation

- Semaphore does not receive a Cloudflare API token, Vault password or private
  certificate/key. A site-ID-validated, nonce-scoped watcher on the local runner
  may invoke **only** `sudo -n /usr/local/sbin/dh-cloudflare-pve-haos-mtls
  --apply` on the PVE host using pinned host-key and SSH identity.
- The PVE worker reads root-only pinned config:
  `/etc/digitalhouses/cloudflare/site.json` (existing),
  `/etc/digitalhouses/cloudflare/haos-mtls.json` (new; exact
  site/VMID/IP/hostname and explicit publication approval), and
  `/root/.config/digitalhouses/cloudflare/api-token.secret` (existing).
- Its private certificate/key and password-protected PKCS#12 bundles use
  `/etc/digitalhouses/cloudflare/haos-mtls` with directory mode `0700`
  and file mode `0600`. Never export these bytes into Semaphore or logs.
- The `dh-haos-mtls.path` systemd watcher must be installed by the trusted
  provisioning/bootstrap process, **not** by the unprivileged Semaphore job.
  Its PVE counterpart requires a narrowly scoped sudoers allowance for the
  exact script and `--apply` argument. No arbitrary CLI script arguments.
- The two new programs and watcher are checked into public `dh_ansible`,
  but **no real secret** is included.
- The existing `bootstrap/cloudflare-commission.py --site <site> --ctid <runner_ctid>`
  now installs the root-only HAOS mTLS PVE executor, the narrowly scoped
  `ansible-linux` sudoers rule, and the runner's systemd watcher alongside the
  established Cloudflare cutover worker. Commissioning does **not** create a
  client certificate, WAF rule, Access app, DNS entry, ingress route, or HAOS
  origin configuration. On an already-commissioned site, use the same
  installer with `--haos-only` to preserve task-10 executors byte-for-byte.
  It refuses to enable the watcher while a pending request exists.
  The PVE `haos-mtls.json` and approved DR Vault
  recovery remain separate controlled prerequisites.

## Recovery and certificate delivery

The root-only PVE material directory is **runtime state**, not a backup.
Before production enablement, generate the household key, CSR and PKCS#12
password into an encrypted candidate of the existing Fleet Vault (no plaintext
Git commits), merge the ciphertext to private Git, and independently verify
recovery from a fresh Git clone using the operator-held master password.
Only then may a trusted one-time commissioning operation stage those exact
restored bytes on PVE as `client.key`, `client.csr` and `p12-password`
(mode 0600), along with a root-only `vault-escrow.json` receipt containing
site, canonical hostname, pinned Fleet Git commit, ciphertext SHA-256 and
SHA-256 digests for all three restored files. Its `recovery_verified: true`
is an operator attestation of the fresh-clone check; it is **not a
cryptographic proof by itself**. The task-70 root worker validates file
hashes, CSR signature/CN and RSA-2048 key matching the CSR before any
Cloudflare mutation; it never issues without the prepared material. On
rotation, use a distinct approved generation, never silently replace files.
Until this human-held master-password and private Git step has been accepted,
**leave** `approved_for_publish: false`.

The modern PKCS#12 and optional Apple-compatible PKCS#12 represent **the same**
one household certificate. Transfer the selected bundle and its password
out-of-band only to authorized devices; import it into Home Assistant Companion
and reauthenticate. Older Apple compatibility uses SHA-1/3DES for **PKCS#12
container encryption only**, not the issued X.509 key/certificate signature.
Do not expose the compatibility bundle as a public download.

Existing Amir Office is an adopted deployment, not an authorization to
overwrite its tunnel, certificate or live Access/WAF policies. Its live API
readback remains required before approving general rollout.
