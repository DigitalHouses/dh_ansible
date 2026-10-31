# PVE endpoint: observed host address, not VM/LXC addressing convention

**Decision (2026-10-31):** every DigitalHouses site's PVE host keeps its
actual LAN address. VM/LXC numbering/addressing conventions do **not** apply
to the Proxmox host itself. Do not invent a PVE IP from site, VMID, CTID, or
subnet; do not keep a list of address exceptions in Git.

## One authoritative fact for a new site runner

Bootstrap already runs **on the actual PVE host**. It reads the current
IPv4 of `vmbr0` using `ip -4 -o addr show dev vmbr0`, checks that it
belongs to the expected LAN, and passes that observed IPv4 to the
guest-only `bootstrap/task-00-setup.sh`. The guest writes
`/var/lib/ansible/site-data/<site-id>/pve-runtime.yml` with
`pve_observed.ipv4` and `pve_observed.source=bootstrap_vmbr0`,
owned by `root:semaphore`, mode `0640`. **This local runtime fact does not
go to public or private Git.** It is not a password or cloud credential.

The existing private Fleet `pve.yml` remains the source for PVE
configuration other than its IP address: node, API port, known TLS
fingerprint, identities and approved access. When an Ansible task calls
the shared `site_inventory_prepare` for `pve.yml`, it materializes an
effective local `pve.yml` by taking Fleet PVE metadata and **replacing
only `pve.host` with the locally observed IPv4**. It never edits the
private Fleet checkout. All consuming playbooks read this effective file.

`site_inventory_prepare` refuses absent or untrusted local observations
rather than falling back to a possibly stale Fleet address.

## Address changes and trust

Observed **at Bootstrap** is not a permanent promise of reachability.
Before performing sensitive PVE changes, check reachability, expected
PVE node identity and HTTPS/SSH trust using the **effective** endpoint.
An IP change after Bootstrap requires a verified local observation from
the PVE host to refresh `pve-runtime.yml`; no blind network sweep and no
fallback to the previously saved Fleet IP.

Keep a PVE TLS/SSH trust check independent of address selection. A new
host at the same IP is not automatically trusted. A stale fingerprint
should block until the real PVE identity is explicitly recommissioned.

## Scope and release state

The initial implementation covers the Fresh Bootstrap runtime fact
and site inventory consumers that call `site_inventory_prepare` (including
media №80 and acceptance №81). Legacy PVE workers and other playbooks
that load `pve.yml` without this dependency must be audited before being
claimed as migrated.

Existing CT1231 was created before this new runtime record; it has **not**
been retrospectively updated. Full PVE deployment E2E is not yet passed.
This standard does not authorize VM/disk destruction or relax API access.
