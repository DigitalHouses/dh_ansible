# ADR-0019 — Reusable dependencies beneath one-button Semaphore tasks

**Статус:** частично реализовано. Базовый контракт принят для задачи 80.
**Цель:** инженер выбирает **одну пользовательскую задачу**, не запоминает последовательность внутренних действий и не запускает вручную служебные шаблоны.

## Модель

- **Semaphore task template** — операторская точка входа, запуск и единый журнал.
- **Reusable stage (Ansible role/include_role/import_tasks)** — компонент или проверка, вызываемая разными шаблонами без второго запуска Semaphore и дополнительных API-токенов.
- **Workflow** — будущая оркестрация нескольких операторских этапов с зависимостями и gating, когда это действительно требуется (например, provision → install OS → PCI cutover → acceptance). Можно позже использовать штатные Workflows Semaphore или отдельную реализованную orchestration capability, но не вводить самописный движок состояний ради одной VM.

Типовые этапы: `site-data`, `audit`, `plan`, `apply`, `verify`; общий контракт статуса: `READY / BLOCKED / PASS` с указанием причины. Этап подготовки данных должен выполняться **до** загрузки component vars, а не падать на недостающем файле.

## Граница авторизации

- `dh_ansible` — только общий код. `dh_ansible_fleet_detail` — приватные данные, без доступа из обычного пользователя Semaphore к всему репозиторию.
- Существующая установленная версия runner получает site data через **owner-authorized PVE bootstrap**. В PR с reusable provider подготовлена новая безопасная схема: root-worker на runner делегирует проверенному PVE helper запрос только нужного site YAML, получает проверенный Git SHA и атомарно размещает файл в runner. **В действующий runner/PVE этот код ещё не установлен; защищённый GitHub read-credential на PVE также не настроен.**
- До завершения commissioning задача 80 делает точный fail-closed с `SITE_DATA_PROVIDER_NOT_COMMISSIONED` / `SITE_DATA_REFRESH_BLOCKED`, не пишет непроверенные данные.
- Для истинного one-click refresh требуется один commissioned, site-scoped доверенный provider: PVE-side credential и проверяемый commit/scope либо эквивалентный сервис. Его отдельное согласование — prerequisite, **не новая задача для инженера**.
- Гарантии provider: проверенный источник и pinned revision, разрешённые файлы только `inventories/<site>/`, запрет секретов и symlink/необычных типов, staging + atomic replace, owner/mode `root:semaphore 0640`, запрет удаления неперечисленных файлов, повторное выполнение без изменений, postcondition хеши. При конфликте — BLOCKED; никогда не silent overwrite.
- Контракт `site_inventory_prepare` является общей точкой расширения; код развёртывания Media VM не должен самостоятельно решать проблемы GitHub авторизации.

## Текущее состояние задачи 80

`Load site identity → site_inventory_prepare → Load media_vm.yml → fleet_secrets → pve_audit → pve_media_vm_plan`.

В действующем main до принятия provider это read-only сценарий **без** создания VM и без автоматического получения отсутствующих site YAML. Новая кандидатная реализация в PR #29 вызывает reusable root-worker для site-data, а затем существующий PVE Access root-worker (режим MEDIA_PLAN) для GET-аудита оборудования с escrow-токеном. **Новая реализация не установлена и не принята E2E**, поэтому не объявлять её доступной в Semaphore. Дополнительный UI template не создаётся.

## Acceptance будущей синхронизации

1. Новый файл/редакция в private Fleet подхватываются одним запуском задачи 80 без ручной команды или запуска шаблона 05.
2. Отсутствие credentials/подписи/commit вызывает `BLOCKED` до PVE операций.
3. Не происходит загрузки чужих site data или секретов в контекст Semaphore.
4. Повторный запуск при неизменном private Git commit — `changed=0`.
5. Ошибочный/старый/недоверенный источник не перезаписывает прежнюю рабочую конфигурацию.
6. Reusable stage вызывается из другой пользовательской задачи без копирования логики.
