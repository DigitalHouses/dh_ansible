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
| 30 | Runner | `read` | Обычный `semaphore` | Audit; требуются site-data |
| 40 | Runner LXC | `software_manage`, `service_manage` | **Общий site/target Linux administrative identity + Ansible `become`**, `infrastructure_access` | Пример нового контракта; до commissioning BLOCKED |
| 50 | PVE host | `read` | Отдельный PVE API/SSH principal, legacy protected audit worker | Не выдавать root через Linux runner |
| 60 | PVE + NetAlertX guest | `host_manage`, `software_manage`, `service_manage` | PVE admin + guest admin; текущий legacy worker | Требуется audit и site approval |
| 70 | HAOS + Cloudflare | external API/secret/config manage | HAOS/Cloudflare identities, текущий fixed worker | mTLS preflight и guarded apply |
| 80 | PVE + Media VM + raw disk | `host_manage`, `software_manage`, `service_manage`, `filesystem_manage`, `destructive` | PVE admin + guest admin + approvals для VM/disk, текущий protected worker | Особые явные VM/disk choices |
| 81 | PVE + Media VM | `read` | Read-only probe через действующий protected worker | Acceptance; не установка |
| 90 | PVE host | `host_manage`, `filesystem_manage` | PVE admin + site-scoped permissions, текущий protected worker | Добавление NFS storage без изменения backup jobs |

## Обязательное правило для каждой следующей задачи

Независимо от номера Semaphore, добавить в ревью и операторскую документацию:
`target`, `operations`, `identity`, `credential_source`, `approval`, `postconditions`, `privilege_status`. При появлении второго экземпляра того же приложения в другом доме меняются **данные объекта**, не имя privileged worker и не реализация модели доступа.

- На обычном Linux: native Ansible modules + `become` только на нужных tasks/roles. Подключить `infrastructure_access` до первого write.
- На PVE/API: отдельная approved identity, API/SSH/TLS trust. Linux `become` в runner не даёт прав на PVE.
- Нет commissioned доступа — `BLOCKED` до write. Никакой скрытой выдачи `sudo NOPASSWD`, «самообновления №00» в root или нового helper под название приложения.
- Действующие legacy protected workers в №10/50/60/70/80/81/90 не удалять преждевременно: сначала отдельная миграция с эквивалентной авторизацией, проверкой поведения и rollback.
- Категории `operations` описывают риск и intent; они **не ограничивают** root на уровне ОС. Компрометация runner admin identity может затронуть весь target, поэтому требуется контроль доверенного кода и credentials.

## Состояние пилота после новых изменений

Живые результаты №00/05/06 и read-only reuse Fleet после reboot **приняты**. Административный `become` пользователя `semaphore` внутри CT1231 **не commissioned**. Новая №40 в Git — реализация, **не E2E PASS**. Повтор №00 обновляет только каталог и ничего не меняет в модели привилегий.
