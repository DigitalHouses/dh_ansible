# NetAlertX · нативная декларативная задача Semaphore 60

## Источник истины

- **Единственная** задача Semaphore: `60-NetAlertX · Развёртывание`.
- Один общий публичный код: `DigitalHouses/dh_ansible`.
- Site-данные, protected VMID, LXC/IP и три approval gate: **только**
  `dh_ansible_fleet_detail/inventories/<site>/netalertx.yml`.
- Existing production NetAlertX, соседние гости, HA, Docker и Cloudflare
  остаются вне scope.
- Базовая native-версия: NetAlertX `v26.5.4`, commit
  `f894390dfab65c4c8fc605af6ee3fa08d95bd354`.
- Проект у Амира использовался как прототип; legacy-роль не имела
  принятого Ansible clean-install E2E и идемпотентности.

## Архитектура исполнения

Semaphore не имеет PVE root key и не получает privileged inventory.
`request-netalertx-install.yml` читает локальный Fleet desired state
и сверяет runner/site identity. Когда любой gate = false, выводится
`PLAN_ONLY`, запрос не создаётся, infrastructure writes отсутствуют.

После явной установки **двух** подтверждённых Fleet gate
`provision_approved` и `dhcp_exclusion_verified`, Semaphore отправляет исключительно `DEPLOY:<uuid>:<site>` в
`/var/lib/semaphore/dh-netalertx-deploy.request`.

Root-owned systemd `dh-netalertx-deploy.path/.service` вызывает
`/usr/local/sbin/dh-netalertx-deploy`. Исполнитель проверяет
UUID, owner/mode запроса, локальный hostname и site-data, блокирует
параллельные запуски и запускает ровно
`playbooks/deploy-netalertx-pve-from-worker.yml` из public main
с root-owned private SSH identity и строгим host key pinning.

Далее тот же playbook:

1. Сверяет защищённые VMID, Debian 13 template SHA, identity PVE,
   PVE ресурсный бюджет и отсутствие чужого CT/QEMU по целевому ID.
2. Создаёт **только отсутствующий** unprivileged LXC; сначала
   `onboot=0`, `start=0`, `protection=1`, `link_down=1`.
   Existing foreign CT не adopted; нет destroy/recreate/update чужих.
3. После gate включает сеть нового гостя, запускает его, доставляет
   минимальный Python/OpenSSH через доверенную PVE console.
4. Порождает отдельный SSH keypair гостя, читает host public key
   через pinned PVE, фиксирует machine-id + host key root-only ledger.
   При замене guest/host key последующая установка fail-closed.
5. Проверяет работоспособность `nftables` внутри нового LXC до запуска backend. Если правила не удаётся применить, прекращает установку (fail-closed).
6. Применяет **общую** `netalertx_native` роль: pinned upstream Git,
   Python venv, PHP8.4/nginx, systemd, SQLite, штатный ARPSCAN.
7. До старта backend включает `nftables`: прямой TCP/20212 закрыт
   от не-loopback клиентов. Порт 20211 обслуживается nginx, локальный
   `/server/` proxy сохраняется. Firewall failure блокирует backend.
8. Проверяет HTTP/UI/API, SQLite, PHP/systemd, upstream Git и внешнюю
   недоступность TCP/20212. Отдаёт `nonce|PASS` только при завершении
   playbook. Сведения о ходе — в root-only
   `/var/log/dh-netalertx-deploy.log`.

## Состояние внедрения (22.10.2026)

Код staged в DRAFT public PR #6, а Fleet site-diff в DRAFT private PR.
Синтаксис и статические safety-контракты проверяются в CI. В отдельном
live audit CT не обнаружен, Debian template присутствовал; выданный
доступ PVE SSH с pinned known_hosts работал. Первичный запуск от
пользователя Semaphore с текущими тремя false gate завершился `changed=0`
в режиме `PLAN_ONLY`. **Нативный LXC ещё не создан и не принят.**

Отдельная commissioning-операция для уже установленного runner:
`bootstrap/commission-netalertx-worker.sh` из проверенного checkout
устанавливает root-owned script и systemd `.path`. Она не создаёт CT.

## Обязательная production-приёмка

До установки: документированная проверка DHCP scope/exclusion,
отсутствия IP conflict, свободных ресурсов, PVE identities и именно
нового CTID. Факт «не отвечает на ping» не заменяет DHCP аудит.

После установки: настоящий ARPSCAN на site subnet, список устройств,
Web UI через браузер, API 20212 недоступен с LAN, SQLite/настройки
сохранились после гостевого reboot, `systemd` сервисы active.
После второго исполнения root playbook принять `changed=0`.
PVE onboot переводить на production target только после принятия
восстановления. Долгосрочная нагрузка и backup/restore отдельно.

Нельзя считать `PLAN_ONLY`, git syntax-check, текущий public PR или
статический unit-test завершённой работой NetAlertX.

## Отдельная семантика API gate

`api_lan_isolation_verified=false` в Fleet — статус пока не принятой проверки,
а не ручное обещание, что firewall уже работает. После DHCP/provision approval
задача 60 может создавать **только новый** CT, но роль NetAlertX сначала
устанавливает и проверяет nftables, затем запускает backend и независимо
проверяет, что TCP/20212 с runner недоступен. Если unprivileged LXC
не поддерживает nftables, запуск NetAlertX запрещён: API gate остаётся false.
Обновлять `api_lan_isolation_verified=true` допускается только после E2E.
