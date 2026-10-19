# Scope ansimble 2.0

**Статус:** нормативный документ. Дата: 2026-10-05.

Этот документ определяет **чем ansimble 2.0 должен уметь управлять**, независимо от того, что уже реализовано или принято под управление на конкретном объекте.

## 1. Модель состояния

Для каждого направления различаем три независимых статуса:

- **In scope** — функция относится к целевой системе ansimble 2.0.
- **Implemented** — существует общая реализация, прошедшая необходимые автоматические тесты.
- **Adopted** — конкретный ресурс конкретного site прошёл live-аудит, принят под управление и имеет однозначного owner/writer.

Наличие функции в scope **не означает**, что она уже реализована или разрешена к применению. Наличие реализации **не означает**, что ей разрешено менять существующий объект.

## 2. In scope

### 2.1 Proxmox VE

- read-only inventory и health;
- установка и базовый bootstrap новых PVE при отдельно утверждённом процессе;
- API identities и минимальные полномочия runner;
- storage configuration, VM/LXC lifecycle и startup/shutdown policy;
- host configuration, необходимая для управляемой инфраструктуры;
- PCI passthrough и другие аппаратные настройки — только как отдельный high-risk capability;
- обновления и миграции PVE — только через отдельный plan с проверкой совместимости и recovery.

### 2.2 VM и LXC

- декларативное создание, adopt и конфигурация VM/LXC;
- CPU, RAM, disks, network, tags, autostart/startup/shutdown;
- установка и bootstrap гостевой ОС;
- SSH host-key pinning и service identities для Linux;
- QEMU Guest Agent и OS acceptance там, где применимо;
- graceful lifecycle и проверяемое удаление только при явном destructive approval.

Новый guest описывается данными inventory. Отдельный набор provisioning-скриптов для каждого site/VMID не является целевой архитектурой.

### 2.3 TrueNAS

- install/bootstrap и adopt существующих экземпляров;
- pool discovery и безопасное управление lifecycle;
- datasets и каталоги;
- users и groups;
- NFSv4 ACL и filesystem permissions;
- SMB/NFS shares;
- service identities и least-privilege access;
- snapshots, backup-related configuration и recovery/import workflows;
- network/API configuration через поддерживаемые интерфейсы TrueNAS.

**Критическая граница:** операции с pool/disk/ACL относятся к повышенному или destructive risk. Отсутствие объекта в desired state никогда не означает автоматическое удаление данных.

### 2.4 Сеть и доступ

В scope входит конфигурация, необходимая для работы управляемой инфраструктуры:

- site IP addressing и DNS/gateway parameters;
- bootstrap networking VM/LXC;
- маршруты и host networking, если они находятся под нашим ownership;
- Cloudflare Tunnel/Access и аналогичный инфраструктурный remote access;
- API/SSH trust, certificates и host keys;
- проверка конфликтов статических адресов до применения.

Router/switch/AP configuration становится managed только после появления поддерживаемой общей роли/API и отдельного adopt. Ansimble не должен менять домашний/клиентский DHCP, VLAN или routing «по пути» ради установки одного сервиса.

### 2.5 Инфраструктурные приложения

В scope входит **развёртывание и конфигурация сервисов, являющихся частью DigitalHouses infrastructure**, включая уже встречавшиеся в legacy:

- Home Assistant OS lifecycle и инфраструктурные зависимости;
- PostgreSQL;
- Uptime Kuma;
- NetAlertX;
- Media Server stack;
- Intranet Browser;
- NUT/UPS services;
- backup/restore tooling;
- другие повторяемые инфраструктурные сервисы, если для них определён общий reusable contract.

Приложение добавляется как новый тип reusable role/contract. Различия сайтов задаются параметрами; не создаётся отдельная реализация «для одного дома», «для другого дома» и т. п.

### 2.6 Электропитание и lifecycle

- UPS/NUT;
- graceful shutdown;
- PVE guest startup/shutdown ordering;
- dependency/readiness checks;
- измерение реального shutdown/recovery time;
- проверяемые FSD и recovery procedures.

Принудительное выключение, FSD test и действия, способные остановить объект, всегда требуют отдельного плана и окна работ.

### 2.7 Backup и Disaster Recovery

- политики backup/retention;
- конфигурация локальных и cloud backup jobs;
- Backblaze B2 как хранилище backup data, где используется;
- backup VM/LXC, HAOS, TrueNAS config и других управляемых компонентов;
- restore workflows;
- документированный RPO/RTO;
- регулярные restore/drill acceptance;
- bootstrap нового runner и восстановление site из Git + backups.

Git является source of truth для конфигурации и **всех сохраняемых infrastructure secrets в зашифрованном виде**, но **не хранилищем пользовательских backup data**.

Disaster-recovery contract: потеря local runner или его диска не должна приводить к потере credentials. Новый runner восстанавливается из Git; оператор вводит один master password и получает доступ к зашифрованным fleet secrets. Секрет, необходимый для восстановления или штатного управления site, не может иметь единственную копию на runner.

### 2.8 Secrets и security controls

- Ansible Vault как текущий формат encrypted-at-rest для единого tracked fleet Vault;
- один operator-held master password для расшифровки tracked Vault secrets всего fleet;
- master password не хранится в Git и не зависит от конкретного runner;
- site-scoped credentials хранятся как namespaced data внутри единого encrypted fleet Vault;
- обязательные credentials не имеют единственной копии на runner;
- least privilege;
- no plaintext secrets в Git, logs и process arguments;
- TLS/server identity verification;
- SSH host-key verification;
- ротация credentials через отдельный контролируемый workflow.

### 2.9 Operations / SRE

- read-only audit;
- plan/diff;
- apply;
- independent verify;
- status/health;
- drift detection;
- idempotence verification;
- backup/restore acceptance;
- version pinning и controlled upgrades;
- отчёт об unknown/blocked state без silent remediation.

## 3. Out of scope by default

### 3.1 Пользовательские данные как содержимое

Ansimble может управлять **storage structure, permissions, shares, backup policy и restore workflow**, но не управляет смысловым содержимым пользовательских документов, media, фотографий, загрузок, баз Home Assistant и иных бизнес/личных данных.

Например:

- создать dataset и ACL — in scope;
- настроить backup этого dataset — in scope;
- удалить или преобразовать содержащиеся там пользовательские файлы — не штатная функция IaC и требует отдельной data-migration/recovery процедуры.

### 3.2 Неуправляемые внешние системы

Ansimble не получает право изменять router, switch, cloud account, клиентский сервис или appliance только потому, что он обнаружен во время audit. Сначала нужен поддерживаемый контракт и явный adopt.

### 3.3 Ad-hoc администрирование

Одноразовая диагностика, emergency/recovery и ручной bootstrap допустимы, но их наличие не расширяет managed scope автоматически. Повторяемая функция после подтверждения должна перейти в общий role/module.

## 4. Ownership и coexistence

На один ресурс в один момент времени должен существовать **один write owner**.

Во время перехода с legacy:

- устаревшие локальные runner и конфигурации вне новой кодовой базы могут временно оставаться действующими до подтверждённого cutover; новые playbook и bootstrap от них не зависят;
- ansimble 2.0 сначала наблюдает их read-only;
- перед первым write конкретного ресурса фиксируется cutover ownership;
- concurrent writers для одного VM/LXC, TrueNAS resource, Cloudflare object, backup job или другого ресурса запрещены.

## 5. Scope не задаёт реализацию

Этот документ перечисляет capabilities, а не структуру кода.

Обязательное следствие архитектурных принципов:

> Новый экземпляр уже поддерживаемого типа ресурса должен добавляться данными inventory, а не новым кодом.

Примеры:

- новый TrueNAS user → запись в `users`;
- новый dataset → запись в `datasets`;
- новый LXC такого же профиля → site variables;
- ещё один объект DigitalHouses → новый site inventory.

Новый код нужен только для **нового типа поведения или нового поддерживаемого API-контракта**, а не для очередного имени ресурса.

## 6. Приоритет реализации

Scope широкий, но реализация идёт по реальной потребности:

1. foundational audit/plan/apply/verify/status;
2. PVE + runner bootstrap;
3. TrueNAS fresh provisioning/bootstrap, затем users/groups/datasets/ACL/shares;
4. VM/LXC reusable provisioning;
5. lifecycle/UPS/backup/restore;
6. инфраструктурные приложения;
7. остальные network/cloud capabilities по мере появления подтверждённой необходимости.

Приоритет не меняет scope и не разрешает преждевременный apply.
