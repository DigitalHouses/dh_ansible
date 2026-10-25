# Site-data provider · commissioning и эксплуатация

**Статус:** кандидат в PR; не считать установленным на существующих PVE/runner до E2E.
**Назначение:** единая зависимость операторских задач Semaphore, не отдельная кнопка.
**Точки входа:** `roles/site_inventory_prepare` и фиксированный root-worker `dh-site-data`.

## Почему это отдельная capability

- Semaphore клонирует **только публичный** `dh_ansible` и не хранит права на приватный Fleet Detail.
- Приватный `media_vm.yml` и другие разрешённые site YAML нужны задаче **до** Ansible-аудита/применения.
- Первоначальный owner-authorized bootstrap с временной GitHub OAuth-сессией не является постоянной синхронизацией.
- Привилегированный PVE root — доверенная граница для получения **только** `inventories/<site>/<component>.yml` по pinned SHA. Это не Fleet Vault. Глобальный master password и содержимое Fleet Vault не копируются на runner.

## Механизм работы (после commissioning)

1. Оператор нажимает обычную задачу, например 80. Внутренний reusable stage отправляет `SITE_DATA:<nonce>:media_vm` в root watcher runner.
2. Runner worker проверяет identity `ansible-<site_id>`, локальный inventory и root-only SSH/known_hosts, вызывает фиксированный PVE helper. В запросе отсутствуют произвольные URL, пути и команды.
3. PVE helper проверяет реальный PVE и CT1231, использует **только** root-only reader, фиксирует HEAD SHA private Fleet, запрашивает **только** разрешённый компонент из точного site namespace. Проверяет Git blob SHA, размер и YAML envelope.
4. Сверяет содержимое по SHA256. Новый файл доставляет root → CT1231 атомарно; уже совпадающий оставляет неизменным; контролируемое изменение допускается лишь при совпадении текущего файла с ранее зафиксированным digest. Неизвестный drift блокирует автоматическую замену.
5. Worker выдаёт `<nonce>|PASS|CHANGED` или `<nonce>|PASS|UNCHANGED`; failure не позволяет следующему Ansible-этапу запуститься. Секреты GitHub не выходят за PVE.

## Поэтапный ввод в эксплуатацию

Сначала реализуется и проверяется полный функциональный цикл задачи в Semaphore. Общая ревизия текущей модели прав доступа и технического долга запланирована после завершения разработки. Существующие PVE-пользователи и sudo-политики при внедрении компонента не изменяются.

## Однократная комиссия владельца (не операция инженера)

- Публичные файлы worker/PVE-helper и read-only PVE-audit роли поставляются **только** проверенным pinned Bootstrap release. Они не устанавливаются из текущего mutable checkout пользователя Semaphore.
- Владелец должен предоставить PVE **read-only fine-grained GitHub credential**, разрешённый только для `DigitalHouses/dh_ansible_fleet_detail` (`Contents: read`), root-only path `/root/dh-fleet-reader-token` mode `0600`. Реквизит никогда не вставляется в чат, Semaphore task vars, GitHub Actions, playbooks или logs.
- Техническое ограничение GitHub: разрешение Contents:read относится к **целому приватному репозиторию**, а не отдельной папке `inventories/<site>/`. Path/site isolation гарантирует **наш исполняющий код**, а не GitHub token. PVE — доверенный узел владельца; credential запрещён в runner. Если такого scope недостаточно, нужно менять модель Fleet (например, отдельные источники на site), а не заявлять ложную GitHub path-scoping.
- Credential как эксплуатационный секрет должен быть учтён в encrypted Fleet Vault/DR процедуре владельца. До DR-проверки commissioning не считается завершённым. Истечение срока/отзыв/компрометация приводят к BLOCKED и отдельной ротации; никакой fallback к публичному URL.
- Публикация PR и успешный GitHub CI **не устанавливают** workers в запущенные VM/LXC/PVE. Нужен отдельный owner-authorized pinned bootstrap release с E2E; для существующего runner — проверенная in-place commissioning операция без пересоздания CT1231.

## Task 80: защита существующей инфраструктуры

После подготовки `media_vm.yml` задача 80 передаёт `MEDIA_PLAN:<nonce>` **существующему** PVE Access root-worker. Тот использует escrow и readonly audit-token внутри доверенной границы; никаких PVE токенов в Semaphore environment/Vault task нет.

Результат — только `PLAN_ONLY`, даже если audit прошёл. Неснятые блокировки (DHCP, approval, old PCI owners, offline backup targets, образ Debian) отображаются в sanitized JSON. Никаких остановок VM, GPU/SATA reassignment, ISO-деплоя или форматирования.

## Acceptance перед объявлением «одна кнопка работает»

- Live source/commit подтверждён; содержимое site YAML совпадает с source SHA256; root:semaphore 0640.
- Дважды нажать 80: второй source refresh UNCHANGED.
- Выключить/отозвать source auth: task80 BLOCKED **до** любых PVE writes.
- Сымитировать локальный drift: защита блокирует авто-перезапись.
- Проверить, что нет runner GitHub credentials/Fleet Vault и что role `fleet_secrets` не вызывается из Semaphore.
- Audit PVE через существующий root worker, результаты JSON nonce-scoped и содержат актуальные владельцы PCI.
- Убедиться, что legacy PVE guests и SATA data untouched.
