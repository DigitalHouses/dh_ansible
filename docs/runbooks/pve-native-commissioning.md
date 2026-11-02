> **CURRENT OWNER DECISION · 2026-11-02 · NEW CT1231:** ручной запуск PVE host-root commissioning **не допускается**. Настройка PVE после Fresh Bootstrap — строго Semaphore + Ansible (см. [issue #111](https://github.com/DigitalHouses/dh_ansible/issues/111)). Этот документ ниже описывает **исторический** сценарий, по которому был принят предыдущий, позднее удалённый CT1231; не повторять его на текущем CT. Новый CT успешно выполнил Bootstrap, №00 и №05, но PVE trust/SSH НЕ commissioned. №06 ожидает trust, без него будет `PVE_TRUST_NOT_COMMISSIONED`. Нативная Task 08 ещё не реализована. Требуется интегрировать public PVE CA/cert fingerprint/SSH host key в 4-шаговый Bootstrap как исходное доверие и выполнить само подключение штатной задачей Semaphore + Ansible. До реализации — BLOCKED; не обходить строгую проверку SSH/TLS.

# PVE native commissioning

One-time infrastructure enrollment; not a task-specific worker.

Run on the actual Proxmox VE host as root, using a pinned copy of
`bootstrap/commission-pve-runner.py` from `DigitalHouses/dh_ansible`.
Command: `python3 /tmp/dh-commission-pve-runner.py --runner-ctid 1231`.

## Actions

- Validate the real PVE `vmbr0` IPv4, the existing site runner CT, and PVE TLS identity.
- Generate one ED25519 keypair owned by `semaphore` inside the site runner.
- Append only its public key to PVE root authorized_keys, preserving existing keys.
- Pin the PVE SSH host key and current TLS certificate in local runner runtime data.
- Copy the PVE **public** cluster CA certificate from `/etc/pve/pve-root-ca.pem` to the site runner inventory as `pve-root-ca.pem` (root:semaphore 0640). This is trust material, not a secret; the native Semaphore credential check consumes it with Python's validated SSL context and explicit certificate SHA256 pinning. For legacy PVE 8 CA without `keyUsage`, Task 06 only unsets `VERIFY_X509_STRICT`, keeping CA, hostname and pin verification.
- Save read-only disk, PCI, ZFS, guest and PVE storage observations locally.
- Verify noninteractive SSH as the `semaphore` user; no fleet secrets or passwords are copied.

The live PVE address is obtained from the host, never computed from VMID,
hardcoded per site or written as an exception to Git.

The script does NOT create or destroy VMs, modify disks, create ZFS pools,
migrate anything or modify other guests. Repeated use reuses the same SSH key.
On failure it reports `PVE_COMMISSIONING=BLOCKED`.

## Acceptance

Expected: `PVE_NATIVE_SSH=PASS`, `PVE_HARDWARE_AUDIT=PASS`,
`PVE_COMMISSIONING=PASS`. Then run `playbooks/audit-pve-native.yml`
using normal Ansible execution from CT1231. Task 50 in the catalog uses
this native read-only audit after refreshing Task 00.

## Privilege boundary

Root SSH on one PVE host is powerful and is trusted only because the
site runner is controlled by the owner. Protect the runner and its private key.
The key is not available to other sites. Destructive media/disk operations
still require exact device identification and separate validation.

## New-server state (2026-10-31)

Runner CT1231: administrative commissioning PASS.
Actual PVE HTTPS endpoint is reachable, but its TLS fingerprint differs
from historical Fleet metadata. Old protected PVE worker is not installed.
The old media VM Fleet configuration retains the old disk serial/GUID and
Intel GPU/SATA addresses. It must not be applied to new hardware without
the read-only hardware report. Media deploy Task 80 and acceptance 81
still use legacy worker code and are NOT yet migrated or accepted.

## Состояние после нового Bootstrap 2.20 Beta

Чистый Tiny Bootstrap сохраняет PVE IP с `source: bootstrap_vmbr0`, но **не комиссионирует TLS/SSH**. До отдельного host-root commissioning задача №06 сообщает `PVE_TRUST_NOT_COMMISSIONED` и не отправляет введённый пароль в PVE. После commissioning Task 06 проверяет сертификат и проходит PVE `root@pam` ticket + чтение `/api2/json/nodes`. Если PVE использует кастомный HTTPS сертификат либо предъявляет имя, отличное от IP, несоответствие TLS блокирует аутентификацию: запрещено отключать CA/hostname проверки. На PVE 8.4 старый сертификат CA без расширения `Key Usage` может отклоняться Python 3.13/OpenSSL 3 при строгом режиме. Исправление №06 использует локально в процессе `ssl.create_default_context(cafile=...)` и снимает только `VERIFY_X509_STRICT`; перед каждым запросом сверяет SHA256 сертификата с commissioning. Проверка сделана на тестовом PVE HTTPS-сервере с синтетической CA без Key Usage; production E2E ожидается.
