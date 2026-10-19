# ADR-0005 — Site-scoped Proxmox API identities

Статус: **Accepted**. Дата: 2026-10-05.

## Решение

Local runner управляет Proxmox VE через официальный HTTPS API. Постоянный root SSH с runner на PVE не является штатным control path.

Каждый site получает собственные API identities. Credentials одного site не переиспользуются на другом.

## Standard persistent access model

Каждый site после bootstrap имеет три постоянных канала:

1. `ansimble-audit@pve!audit` — read-only PVE API, роль `PVEAuditor` на `/`.
2. `ansimble@pve!write` — штатный write PVE API, роль `PVEAdmin` на `/`.
3. Linux/PAM user `ansimble` — SSH только с IP local runner и passwordless sudo
   для host-level post-install, которого нет в PVE API.

API tokens используют privilege separation. Backing user получает ту же роль на
том же scope, что и соответствующий token.

## Read-only audit identity

Для read-only inventory используется отдельный service user/token с эффективной ролью `PVEAuditor` на требуемом scope.

Audit identity:
- не создаёт/изменяет/удаляет VM/LXC/storage/jobs;
- не используется как будущая write identity;
- token secret хранится только как site namespace внутри encrypted fleet Vault;
- token value не передаётся в Git, logs или chat.

## Write capabilities

Штатные PVE API writes выполняет `ansimble@pve!write` с `PVEAdmin` на `/`.
Audit token никогда не расширяется до write. Более узкие capability tokens могут
существовать только как временная миграционная мера; после принятия общего
write identity они не должны оставаться конкурентными writer-ами.

Host-level изменения (`apt`, `/etc`, NUT, systemd, sysctl и подобные)
выполняются как Linux user `ansimble` через SSH key и sudo. Пароль account
заблокирован; SSH key ограничивается source IP local runner. Private key и API
token secrets имеют authoritative encrypted copy в
`secrets/fleet.vault.yml` согласно ADR-0007.

Встроенная роль Proxmox `PVEAdmin` намеренно не содержит `Sys.Modify`.
Поэтому QEMU lifecycle-поля, для которых Proxmox требует `Sys.Modify` на
корневом scope (в частности accepted VM operational state), не являются
основанием расширять штатный API token до `Administrator`. Такие изменения
выполняются через уже проверенный privileged host transport `ansimble` +
pinned SSH + sudo, с read-before-write и независимой проверкой postconditions.

## TLS

PVE endpoint identity проверяется до API auth по pinned SHA-256 certificate fingerprint (или доверенному site CA после его managed bootstrap). При несовпадении fingerprint любые API requests блокируются.

## Bootstrap exception

Создание первой API identity требует одноразового административного действия на существующем PVE. После появления поддерживаемого PVE provisioning capability новые sites должны получать identities из общего bootstrap workflow.

До создания постоянного Linux/PAM `ansimble` для host-level конфигурации,
которую официальный PVE API не предоставляет, допускается временный bootstrap
transport с local runner:

- controller остаётся site runner с pinned Ansible runtime;
- отдельный временный ED25519 key создаётся только на время одного apply;
- public key временно добавляется root на PVE с ограничением source IP local runner
  и без forwarding/PTY;
- runner проверяет pinned SSH host key и machine-id PVE до первой managed write;
- после apply/verify ключ удаляется независимо от результата;
- temporary private key не хранится в Git, Vault или inventory;
- постоянный root SSH credential не создаётся;
- bootstrap transport запускает только общий playbook/role, а не site-specific shell implementation;
- на Proxmox VE `/root/.ssh/authorized_keys` штатно может быть symlink на
  `/etc/pve/priv/authorized_keys`; bootstrap обязан сохранить эту ссылку,
  проверить exact resolved target и удалять только собственную marker-line,
  не заменяя весь authorized_keys файл.

Это исключение не меняет основной control path: поддерживаемые PVE resources
управляются через API и capability-scoped identities.

## Fleet rule

Site-specific values (host, node name, certificate fingerprint, token principal) находятся в `inventories/<site>/`; API behavior реализуется общей ролью/module.
