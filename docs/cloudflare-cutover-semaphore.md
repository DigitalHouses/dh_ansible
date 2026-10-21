# Cloudflare connector cutover via Semaphore

## Contract

**One site, one remotely managed Tunnel, one existing PVE cloudflared
connector process.** Do not create a second systemd cloudflared service.
Do not delete the old tunnel or automatically migrate its other routes.

Private Fleet \`inventories/<site>/cloudflare.yml\` is the recorded source for
the observed old routes and ports. The public root-worker scripts derive
site-specific IDs from protected local one-time enrollment only.

- Task 10: nonprivileged, read-only local PVE, public DNS and Access challenge.
- Task 11: operator-initiated fixed-purpose request on local Semaphore;
  new root-owned runner worker invokes one pinned PVE host script over
  existing host-key-verified \`ansible-linux\` SSH with \`sudo -n\`.
- API management token is enrolled once as root on PVE, mode 0600.
- The connector token is retrieved through Cloudflare API on PVE (GET),
  stored on PVE as root-only mode 0600, and referenced with
  \`cloudflared tunnel run --token-file\` on the **same service**.
- New DNS, ingress and wildcard Access **must already exist** and be
  independently verified. This implementation NEVER creates/edits them.
- A readback of healthy Tunnel API status and active service is required
  before Task 11 reports success.
- If restart or API health fails, attempt removal of only the dedicated
  systemd drop-in and restart the pre-existing service.
- Post-authenticated PVE browser check remains manual; an anonymous 302
  does not demonstrate that a service is reachable behind Access.
- Default prior \`noTLSVerify\` configuration is not changed by this cutover.
  Site owners should separately plan proper origin TLS trust.

## One-time commissioning

**Do not** add the private Fleet repository as a Semaphore Git repository.
Do not give Semaphore the Cloudflare management token, SSH private key or
PVE sudo access.

On the site's PVE console (root), obtain a **pinned**, CI-accepted public
\`dh_ansible\` checkout. Run:

\`\`\`bash
python3 bootstrap/cloudflare-commission.py --site <site-id>
\`\`\`

This installs only the fixed host executor and CT1231 root watcher; it does
**not** switch tunnels. All new root-controlled files require source/target
identity checks and must not overwrite unknown local files.

Then, on the same PVE host, enroll the site's Cloudflare metadata and API
token once via the hidden prompt (all parameter values from private Fleet;
old name is the literal observed Cloudflare name):

\`\`\`bash
/usr/local/sbin/dh-cloudflare-pve-cutover --enroll \
  --site-id <site-id> --pve-host <site-pve-ip> \
  --account-id <cf-account-id> --zone-id <cf-zone-id> \
  --old-tunnel-id <old-uuid> --old-tunnel-name '<old-name>' \
  --new-tunnel-id <new-uuid> \
  --access-app-id <wildcard-access-id> \
  --access-policy-id <reusable-policy-id>
\`\`\`

No command-line parameters include token or password. The script checks
the existing old and new Tunnel, canonical single route, exact DNS CNAME,
wildcard Access and attached administrator policy. Enrollment does not
modify either tunnel or the connector.

In Semaphore run task 00 to obtain template 11; run task 50 to accept
the existing PVE SSH/sudo identity; manually run template 11.
After PASS, verify browser login to the new canonical PVE hostname.

The accepted immutable bootstrap release is unchanged by installing this
on-demand component; fleet-wide first-install integration is a future,
independently accepted milestone.
