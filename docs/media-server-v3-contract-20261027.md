# Media Server v3 — implementation contract (27 October 2026)

Status: REQUESTED / NOT DEPLOYED. Target site: serzhan-home. VMID 2238 and IPv4 192.168.11.238 remain unchanged. Current working deployment is keep + import. No VM deletion or ZFS wipe is authorized.

## 1. DigitalHouses Plex Agent
Deploy the canonical native Linux product `digitalhouses_plex_agent` next to Plex, pinned to a verified stable release of DigitalHouses/home-assistant-apps. The upstream first-run installer is interactive unless /etc/digitalhouses_plex_agent/digitalhouses_plex_agent.conf already exists: pre-provision its MQTT host/user/password securely from private Fleet, mode 0640. Avoid leaking MQTT credentials to Semaphore or the public repository. Acceptance: agent active, MQTT connected, expected Home Assistant Discovery entities visible.

## 2. LAN discovery
Windows Explorer: ensure wsdd2 remains active and advertises the SMB server on the LAN. macOS Finder: install avahi-daemon and publish _smb._tcp on port 445. Verify both on actual Windows and macOS clients. Keep Samba NetBIOS name <=15 characters; it need not equal the Linux hostname.

## 3. In-place naming
Desired PVE VM name and Debian hostname: `mediaserver-serzhan`. Current managed name: `media-server-serzhan-home`. Update inventory, PVE ownership checks, managed rename mechanism and test contracts as a single controlled change. On `keep`, safely accept the exact verified legacy VMID 2238 and rename it with `qm set --name` without touching disks. Do not change VMID, IP, MAC or storage. Update discovery display name and portal label. Generic site naming must use canonical owner identity, never a lossy string split.

## 4. Accounts, passwords, and shares
Initial accounts only:
- `serzhan` — site owner, `media_group`, `doc_group`; owner access to Media and Documents shares.
- `serzhan_admin` — DigitalHouses administrator at every site, member of all approved application groups (media_group, doc_group, backup_group, download_group, admin_group), authorized for all client-facing SMB shares. Not a Linux root/sudo/Docker operator.
- Client-facing passwords must be no more than 8 characters per explicit user requirement. Recommend exactly 8 randomly generated characters, unique for each account and site; never embed literals in public code. Short passwords are a documented security risk; permit LAN/VPN access only, not public SMB ports.
- Provision POSIX and Samba identities consistently, preserve/rotate credentials idempotently, avoid writing secrets into logs/command args/Git public repository.
- Export individual handoff credential files beneath the ZFS `digitalhouses` dataset, never the public HTTP portal. Ensure strict per-user/admin-only ownership and access; never expose the global administrator password in the ordinary owner's public file/share. Provide a secure bootstrap retrieval route for the operator.
- All user-accessible SMB shares deny guest login; validate upload/download and reader/writer access. Do not expose the PVE NFS backup export through SMB merely because the admin requests all shares.
- Additional users and web-based user management are postponed. Neither Filestash nor Sambee is assumed to provide Linux/Samba account administration.

## 5. PVE backups mapped to guest backups dataset
Dataset: `serzhan-home-pool/backups` inside media VM. Export via NFS only to the canonical PVE address, 192.168.11.30; create a Proxmox NFS storage with content=backup and explicit, unique storage ID, after independent preflight and privilege-bound worker. Restrict clients and permissions. Do not configure a backup job for VM 2238 to write only to that VM-hosted export: stopping or losing the media VM removes its own backup target. Keep an independent destination for the media VM/host disaster recovery; do not silently modify existing jobs. Test mount availability, writes and restoration using a disposable backup.

## 6. Users later
Future web UI must manage the same POSIX/Samba accounts and group rules with strict administrative authentication and audited changes; it must not create a conflicting secondary identity database.

## 7. Local portal at port 80
Serve a lightweight responsive site index at http://192.168.11.238/ listing only enabled services: Plex 32400/web, Jellyfin 8096, Filestash 8334, Sambee 8000; separate clearly labelled admin links Radarr 7878, Sonarr 8989, Prowlarr 9696, qBittorrent 8080. Do not embed credentials or act as an authentication proxy. Portal is LAN-only; each app must still enforce its own authorization. In particular, remove unsafe LAN-wide qBittorrent authentication bypass before publishing admin links to all household users. Port 80 availability and startup persistence are acceptance gates.

## Rollout gates
1. Verify source inventory and private Fleet secret model without displaying passwords.
2. Add migrations/tests in a feature branch; syntax-check, regression tests and read-only preflight.
3. Review exact VM rename, NFS permissions, app auth, secrets export and restore contingency.
4. Apply to existing VM only using `keep` + `import`.
5. Test Windows/macOS discovery and upload/download, web client uploads, 8-char account policy, portal, Plex Agent MQTT and backed-up recovery.
6. A destructive `recreate` + `erase` E2E requires a separate, explicit decision.


## Application authentication implementation status

The branch now contains an idempotent Filestash SMB passthrough configuration reconciler, using the upstream-supported `username_and_password` authentication middleware. Each user signs in with their Samba credentials, and the application enumerates only the shares Samba grants them. Existing Filestash general configuration is preserved; unknown custom storage backends block rather than being overwritten.

Filestash is intentionally prevented from starting without an operator-provisioned root-owned 0600 `/etc/digitalhouses/filestash.env` containing a bcrypt `ADMIN_PASSWORD` hash. The protected secret injection from the encrypted Fleet Vault is **not yet implemented**.

The Jellyfin startup helper uses the official `Startup/User`, `Startup/RemoteAccess`, `Startup/Complete` and `Users/AuthenticateByName` API operations. It requires a root-owned 0600 administrator JSON passport in `<pool>/digitalhouses/credentials/apps/jellyfin-admin.json`. A test with a mocked local API verifies initial completion and repeat-run idempotence. Provisioning the secret from Vault and configuring authenticated media libraries remain pending.

qBittorrent no longer has automatic authentication bypass for the entire LAN in the draft Ansible role: its passwordless API remains limited to loopback for the installed Arr integration. Permanent authenticated WebUI credentials still need a protected provisioning flow before the change may be applied to the live server.

**Release gate:** Private Fleet PR #13 has no approved protected MQTT or application administration credential delivery mechanism. Do not mark this PR ready, do not merge to production or run Semaphore task #80. A dry syntax check and offline mocks are not evidence of a real E2E application acceptance.
