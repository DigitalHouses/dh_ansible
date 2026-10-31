# Реестр требований к полномочиям Semaphore задач

**Статус:** нормативная классификация операций; **не является** подтверждением фактически установленного доступа.  
**Источник:** публичный каталог `catalog/semaphore-tasks.json` на 2026-10-31.  
**Архитектура:** [ADR-0020](../decisions/0020-infrastructure-privilege-and-software-installation.md), [Semaphore task standard](semaphore-task-design.md).

## Для всех задач

| № | Managed target | Тип операции | Административный доступ / credentials | Применение |
| --- | --- | --- | --- | --- |
| 00 | Semaphore project на runner | catalog metadata manage | Local API token **root-only**, действующий fixed catalog worker | Только catalog, не установка ПО |
| 05 | GitHub → локальная папка Semaphore | private Git read / credentials bootstrap | GitHub Device Flow пользователя `semaphore`, без `become` | OAuth + checkout |
| 06 | Локальная credential store runner | Vault credential verify/store | Одно скрытое значение, `semaphore`, без `become` | Не создаёт sudo |
| 10 | PVE host и Cloudflare | `software_manage`, `service_manage`, external API | Отдельный PVE/admin + Cloudflare principal, **не** root внутри runner; действующий legacy fixed worker | План/apply с защитой Cloudflare |
| 20 | Runner | `read` | Обычный `semaphore` | Smoke |
| 30 | Runner | `read` + site-data reconcile | Локальный `semaphore`, native sudo только при materialize | Inventory `runner.yml` из приватного Fleet |
| 40 | Runner LXC | `software_manage`, `service_manage` | **Локальный `semaphore` + `sudo NOPASSWD: ALL` внутри runner**, обычный Ansible `become` | В Git реализовано; CT1231 retrofit/Online E2E ещё не принят |
| 50 | PVE host | `read` | Отдельный PVE API/SSH principal, legacy protected audit worker | Не выдавать root через Linux runner |
| 60 | PVE + NetAlertX guest | `host_manage`, `software_manage`, `service_manage` | PVE admin + guest admin; текущий legacy worker | Требуется audit и site approval |
| 70 | HAOS + Cloudflare | external API/secret/config manage | HAOS/Cloudflare identities, текущий fixed worker | mTLS preflight и guarded apply |
| 80 | PVE + Media VM + raw disk | `host_manage`, `software_manage`, `service_manage`, `filesystem_manage`, `destructive` | PVE admin + guest admin + approvals для VM/disk, текущий protected worker | Особые явные VM/disk choices |
| 81 | PVE + Media VM | `read` | Read-only probe через действующий protected worker | Acceptance; не установка |
| 90 | PVE host | `host_manage`, `filesystem_manage` | PVE admin + site-scoped permissions, текущий protected worker | Добавление NFS storage без изменения backup jobs |

## Обязательное правило для каждой следующей задачи

Независимо от номера Semaphore, добавить в ревью и операторскую документацию:
`target`, `operations`, `identity`, `credential_source`, `approval`, `postconditions`, `privilege_status`. При появлении второго экземпляра того же приложения в другом доме меняются **данные объекта**, не имя privileged worker и не реализация модели доступа.

- На обычном Linux: native Ansible modules + `become` только на нужных tasks/roles. На собственном runner локальный `semaphore` получает sudo без отдельного пароля; на других Linux узлах права выдаются независимо. Подключить `infrastructure_access` до первого write.
- На PVE/API: отдельная approved identity, API/SSH/TLS trust. Linux `become` в runner не даёт прав на PVE.
- Нет commissioned доступа — `BLOCKED` до write. Явное исключение `sudo NOPASSWD` принято для disposable runner LXC (не PVE и не чужих VM); №00 не выдаёт эти права и не заменяет Bootstrap/retrofit.
- Действующие legacy protected workers в №10/50/60/70/80/81/90 не удалять преждевременно: сначала отдельная миграция с эквивалентной авторизацией, проверкой поведения и rollback.
- Категории `operations` описывают риск и intent; они **не ограничивают** root на уровне ОС. Компрометация runner admin identity может затронуть весь target, поэтому требуется контроль доверенного кода и credentials.

## Состояние пилота после новых изменений

Живые результаты №00/05/06 и read-only reuse Fleet после reboot **приняты**. Административный `become` пользователя `semaphore` внутри CT1231 **не commissioned live**. В Git реализованы fresh installer и reusable retrofit-роль. №40 **не E2E PASS**. Повтор №00 обновляет только каталог, не выдаёт права. После №05 site-component YAML доставляется одной `site_inventory_prepare` из локального приватного Fleet checkout — отдельный `dh-site-data` worker не нужен.

## Операционный аудит после упрощения — 31.10.2026

| Задачи | Проверенный факт / оставшийся блокер |
| --- | --- |
| 00, 05, 06 | **LIVE PASS** для Fresh E2E и reuse после reboot. Ничего не переустанавливать. |
| 20 | Безопасный smoke; отдельной привилегии не требует. |
| 30 | Теперь автоматически берёт `runner.yml` из локального Fleet через общую роль. Требуется локальный sudo и live acceptance. |
| 40 | Нативный install через `become`. Требуется retrofit CT1231, запуск и Online/pairing acceptance. |
| 10 | По-прежнему требует legacy `dh-cloudflare-switch.path`, которого нет в Fresh Bootstrap. Централизация CF — **отдельная будущая работа**, без передачи глобальных токенов домам. |
| 50 | Требует legacy `dh-pve-access-audit.path`; после Fresh Bootstrap worker отсутствует. |
| 60 | `netalertx.yml` теперь загружается из Fleet, но установка всё ещё требует legacy `dh-netalertx-deploy.path` и PVE capabilities. |
| 70 | `haos.yml` теперь загружается из Fleet, но apply требует legacy `dh-haos-mtls.path` и Cloudflare privileges. |
| 80 | `pve.yml` и `media_vm.yml` теперь синхронизируются из Fleet. Реальное VM/disk apply по-прежнему требует PVE worker и approvals. |
| 81 | Нужные site YAML доступны через общий механизм, но read-only acceptance требует PVE worker. |
| 90 | Подключение PVE NFS по-прежнему требует PVE worker, а также собственную проверку export/storage. |

**Рабочий путь по приоритету:** (1) подключение локального sudo на CT1231;
(2) проверка общей site-data роли; (3) №30 и №40; (4) единый PVE target
access и поэтапная миграция 50/60/80/81/90 с сохранением risk approvals;
(5) Cloudflare 10/70 через отдельную централизованную identity. Нельзя
считать оставшиеся задачи рабочими только потому, что их шаблоны есть в UI.
