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
Serve a lightweight, responsive, Russian-language **client homepage** at http://192.168.11.238/ (VM port 80). Display and explain **all enabled on-VM applications** in three functional groups: watching/listening (Plex 32400/web, Jellyfin 8096), browsing/uploading/downloading files (Filestash 8334, Sambee 8000), and managing content/downloads (Radarr 7878, Sonarr 8989, Prowlarr 9696, qBittorrent 8080). Show the local media management links regardless of `portal.show_admin_links`; that flag controls only *infrastructure admin links* (PVE, Semaphore), which are hidden by default in the site inventory. Include explicit Windows and macOS SMB connection examples, and a reminder to bookmark the portal. Render only services marked enabled in Fleet. Publish DNS-SD `_http._tcp:80` along with `_smb._tcp:445` using the existing Avahi service definition, without a second mDNS daemon. Do not embed passwords, pretend the homepage is SSO, or expose internal technical secrets. The portal is LAN-only; each linked app independently enforces its own authorization (including authenticated qBittorrent LAN WebUI). `nginx` HTTP 200 plus presence of every enabled media application link is an acceptance gate.

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


## Media v3 final application onboarding revision

- Public code branch now provisions three application administrator account TXT files in the already-approved `<pool>/digitalhouses/credentials/apps/`, preserving existing files on rerun. User SMB accounts stay in `<pool>/digitalhouses/credentials/`.
- Filestash uses Samba login without a duplicate web-user database and receives its administrative bcrypt hash automatically from the local app credential file.
- qBittorrent is changed from LAN-wide anonymous API access to authenticated LAN WebUI; loopback access remains available to local Arr integrations. Administrator password is applied only once, with an idempotent stamp.
- Jellyfin now initializes the admin account noninteractively, verifies authentication, and creates three media libraries `Movies`, `TV Shows`, and `Music`, using existing ZFS library paths without resetting existing valid entries.
- DigitalHouses Plex Agent uses only the fixed-purpose site MQTT runtime, never an unencrypted private Fleet inventory password. The approved encrypted MQTT credential is stored in the private Fleet Vault main branch, commit `8c431b6b21d8b95f64081237db721090c2bd6e3b`.
- A one-time owner-controlled runtime commissioning tool reads the encrypted Vault from GitHub on PVE, prompts for its *existing* master password, and creates only a root-owned 0600 site-runtime file on the PVE host. This is the already approved ADR 0009 mechanism. It does not ask for MQTT credentials again.
- The fixed-purpose Semaphore deploy worker requires a scoped MQTT export **before any VM mutation**, writes the short-lived copy root:root 0600 under `/run`, forwards only that MQTT namespace to the Ansible guest setup, and removes the temporary file on exit.
- The pinned PVE installer downloads and stages the complete media guest role rather than relying on incomplete static paths. Installation still requires the accepted, pinned worker rollout.

Offline regression coverage currently includes application password persistence, qBittorrent authenticated LAN policy and idempotence, encrypted Vault MQTT extraction, per-site PVE runtime persistence, nonsecret Fleet preflight, installer staging completeness, and three Jellyfin media libraries.

**No live E2E has been executed.** Both public and Fleet data-only PRs remain draft. Until code and Fleet are coordinated, PVE root helper is installed from the pinned source, local runtime is commissioned, and a read-only PVE acceptance passes, **Semaphore task #80 must not be applied**. Existing VMID 2238 and the live ZFS datasets have not been changed by this branch.

## Administrative SMB access to credential handoff files

The owner manually sends existing `/digitalhouses/credentials/*.txt` and `/digitalhouses/credentials/apps/*.txt` using WhatsApp. The root-owned ZFS records are readable over the *administrator-only* `[DigitalHouses]` Samba share by `serzhan_admin` (group `admin_group`) without logging into Debian as root. The `credentials` and `credentials/apps` directories use `root:admin_group 0750`; account TXT files use `root:admin_group 0640`. Ordinary owner `serzhan` is not a member of `admin_group` and is not permitted into the share. Internal synchronization stamps and service-facing hashes remain root-only. Existing root:root 0600 account TXT files are preserved and permissions reconciled without rotating their passwords.

No passcodes are rendered on the anonymous HTTP portal, pushed into public Git, or logged by Semaphore. The user explicitly elected to forward credentials via WhatsApp without adding a separate delivery system.


## Approved unified client login · implementation update (2026-10-27)

The **single client-facing** username is the Fleet owner identity, for this site `serzhan`. The authoritative password is the existing protected ZFS file `/serzhan-home-pool/digitalhouses/credentials/serzhan.txt`, generated once as **eight alphanumeric characters**. Voluntary later changes may choose **6–8 alphanumeric characters**; qBittorrent requires at least six. No application-specific client password files are generated, and a rerun reads the current owner value rather than regenerating it. The separate `serzhan_admin` POSIX/Samba identity is a DigitalHouses operator identity, not a second password the household must use for its media applications.

Initial application adapters (in the public code PR only, **not production verified**):
- Windows/macOS SMB and Filestash's Samba passthrough use the owner Samba credential. Filestash's configuration-admin bcrypt hash is initially derived from the same owner value but never printed.
- Jellyfin wizard uses the same owner username/password, verified by the native local authentication endpoint, with safe existing-library reconciliation.
- qBittorrent WebUI uses that owner credential; Arr download API is intentionally permitted only over loopback.
- Radarr/Sonarr/Prowlarr use native Forms authentication via their host-config APIs; owner credential is supplied only on first managed commissioning and an unrelated configured login blocks rather than being overwritten.
- Sambee is configured with the owner name before first startup; the initial random upstream admin password is read only in-process from Docker startup logs and changed once via Sambee's native password API. Existing independently configured Sambee identities block rather than being overwritten. Sambee SQLite is never modified directly.
- Plex maintains its independently authenticated Plex account. MQTT credentials and app-to-app API keys remain separate.

The new app owners use root-only synchronization stamps that prevent normal Ansible reruns from changing credentials. These stamps and service-specific API state need a dedicated controlled *rotation* workflow. **Password rotation from the web portal is not implemented yet**: it requires a protected HTTPS origin, authenticated old-password challenge, CSRF protection, rate limiting, serialized transaction with staged state/rollback or resume, and independent verification across all enabled services. Never expose a password-change form over the existing plain HTTP port 80.

**Release blocker:** This branch is not yet ready for `80 keep + import` as a completed client experience. Automated native APIs for Radarr/Sonarr/Prowlarr and Sambee are currently validated by offline fake-response tests, **not real installed application responses**. They must be tested against the exact pinned upstream releases and fail closed on incompatible API shapes. No production VM/CT/password was modified by this update.

 
## Unified new-guest Bonjour and owner-pool create-only enforcement

The reusable role `guest_network_discovery` is now part of the pinned installer
and applies to (1) the approved Media Server Debian VM, (2) a newly provisioned
NetAlertX Debian LXC, and (3) a newly created Ansible runner Debian LXC.
For each new guest it checks the exact hostname, installs Avahi only where the
selected provider is `avahi`, and independently requests `<hostname>.local`
from another same-LAN host using multicast, checking that the reply source
and A record both match the declared actual guest IPv4 address. Duplicate
application-specific Avahi installation was removed. Existing other VMs/CTs
are not bulk modified. Native appliances retain their own responder support.
Real multicast E2E has not yet passed; offline contracts and Ansible syntax
checks are not a substitute.

ZFS naming: on a newly CREATED pool (`raw disk action = erase`), the pool name
must match `site.owner_slug + '-pool'` such as `serzhan-pool`. A verified
`import` keeps the exact legacy name/GUID in the Fleet inventory such as
`serzhan-home-pool`. The media storage reconciler and planner no longer
mistake importing legacy pools for pool creation. Existing mounts/datasets,
NFS/SMB exports and all media paths remain anchored to `storage.pool_name`;
there is **no automatic pool rename**.

Offline acceptance on the Ansible runner: 45 PASS, 0 FAIL, one fixture-specific
test not run. Syntax checks PASS for media planning, media guest setup,
NetAlertX installation, and new-runner commissioning. PVE VM2238 and live
ZFS were never modified by this PR.


## Root-only voluntary customer password rotation: staged primitive, NOT public UI

The approved optional password change (customer MAY change, never forced) is
implemented as a staged local-only primitive in draft PR #55:

- dh-media-password-transaction.py: root-owned write-ahead JSON journal
  (0600), single process lock, durable fsync and atomic stage commits,
  staged native login verification, rollback on partial operations, explicit
  resume after process crash. A committed password is never silently reverted
  merely because an application temporarily cannot be verified.
- dh-media-password-adapters.py: official native authentication APIs for SMB,
  Jellyfin, Sambee, Radarr/Sonarr/Prowlarr, qBittorrent, and Filestash.
  qBittorrent checks the LAN WebUI IP (not exempt localhost). Filestash
  checks the on-disk bcrypt hash and the running service hash. HTTP API
  requests disable proxy forwarding.
- dh-media-password-rotate.py: fixed root-only site manifest and privileged
  entrypoint; old and new passwords enter through bounded STDIN JSON only,
  never argv, environment variables, Git, or HTTP access logs. Service and
  Samba stamps reconcile after app updates; credentials/<owner>.txt commits
  last. Plex, MQTT, and inter-app API keys are not rotated.
- Pending ZFS journal blocks ordinary guest Ansible apply before application
  mutation. The HTTP port 80 site is asserted to remain static with no
  FastCGI or reverse-proxy password mutation endpoint.

**Not yet commissioned:** no password-change form or listener is enabled.
Before allowing client-initiated changes, provision a trustworthy HTTPS
certificate for client browsers; authenticated session and CSRF protection;
rate limiting; request size/time bounds; no secret-bearing access logs;
a constrained privilege bridge to the root-only CLI; and interrupted
operation recovery. Untrusted self-signed certificates with browser warnings
are not accepted as normal client UX. Enable the voluntary portal control
only after live pinned-version E2E verifies all services, rollback, and
repeat Ansible apply without resetting the customer password.

Offline adapter/API simulations do not constitute live password rotation
acceptance. VM 2238, CT1231 and physical ZFS remain untouched.


## Аудит блокеров VM 2238: DHCP и отложенный PVE backup (2026-10-27)

- Владелец подтвердил на ASUS диапазон DHCP `192.168.11.50–192.168.11.229`. Адрес медиасервера `192.168.11.238/24` находится вне него. Fleet объявляет точные `network.dhcp_pool.first_host/last_host` и `static_ipv4_verified=true`. Код проверяет диапазон и выход статического адреса за его пределы, а PVE/гость дополнительно проверяют фактический IPv4 и MAC.
- Старое PVE-хранилище `truenas_backup` недоступно, поскольку физический диск передан медиасерверу. Единственное зависимое задание `dh-backup-critical-daily` использует явный список `110,200,333,501,1011,10001,10004`; **VM 2238 отсутствует**. Пока назначение не исправлено, резервирование этих гостей не работает.
- Для уже существующей VM допускается одно узкое исключение: только `keep + import`, только один конкретный offline storage из Fleet, только одно явно указанное активное backup-задание со строгим перечнем VMID **без медиасервера**. В read-only PCI-плане остаётся `PVE_LEGACY_BACKUP_MIGRATION_DEFERRED`. При других недоступных storage, назначении `all`/pool, отсутствии точного списка VMID, включении VM2238 или выборе `erase/recreate` — **BLOCKED**.
- Механизм планирования передаёт `VM_ACTION` и `DISK_ACTION` через nonce-scoped фиксированный запрос в доверенный PVE audit. PVE формирует и возвращает те же действия; Semaphore проверяет их точное совпадение перед apply.
- На этапе media guest provisioning Ansible подготавливает NFS-экспорт `/serzhan-home-pool/backups/pve`. PVE **не добавляет** storage автоматически в задаче №80. После полной приёмки медиасервера отдельно проверить NFS и смонтировать его в PVE под `dh_media_backups`, затем с отдельным разрешением переназначить `dh-backup-critical-daily` со старого `truenas_backup`; проверить успешную резервную копию и восстановимость. Старое задание не отключать и не удалять до согласования.
