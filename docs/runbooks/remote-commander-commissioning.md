# Задача №40 — Remote Desktop Commander как обычная Ansible software installation

**Архитектура:** [ADR-0020 — общие инфраструктурные полномочия](../decisions/0020-infrastructure-privilege-and-software-installation.md).  
**Статус на 2026-10-31:** новая реализация в Git, **не принята live E2E**, инфраструктурный `become` на существующем CT1231 ещё не commissioned.

## Зачем больше не нужен специальный worker №40

Установка приложения — штатное действие Ansible над Linux host. В новом `playbooks/request-runner-remote-commander.yml`:

1. Проверяется `site.id` и то, что запущен именно локальный runner.
2. Общая роль `infrastructure_access` проверяет административный доступ к текущему target (`become: true`).
3. Существующая роль `runner_remote_commander` устанавливает **закреплённые версии** Node.js и Remote Desktop Commander, настраивает/включает systemd service средствами Ansible.
4. Playbook выводит **только отфильтрованный** одноразовый код из журнала этого invocation либо `ALREADY_AUTHORIZED`.
5. После подтверждения кода на `https://mcp.desktopcommander.app/device/verify` AI-агент отдельно проверяет, что данный runner отображается `Online`.

Отдельных `dh-remote-commander-install.path`, `INSTALL:<uuid>`, root-owned staging и скриптов обновления **для этого playbook не требуется**. Исторические файлы таких handlers могут пока оставаться в исходниках для совместимости, но их не применять в новой схеме.

## Требование к доступу — общее, не «под №40»

Для установки и управления системными сервисами на site runner необходим **commissioned административный доступ Ansible к runner LXC**. Это общий инфраструктурный prerequisite для любого `software_manage` / `service_manage` на данном Linux host.

№05/06 обеспечивают авторизацию GitHub и расшифровку Fleet Vault; они **не создают sudo credentials для Linux**. На текущем CT1231 `semaphore` является непривилегированным пользователем. Нельзя считать `become` готовым, пока он реально не подтверждён.

Выбор учётной записи, способа `sudo/become`, хранения credentials в site-scoped Vault и уровня доверия исполняемому Git SHA — **отдельная общая инфраструктурная операция, не настройка №40**. Не выдавать `sudo NOPASSWD: ALL` автоматически публичному mutable playbook без решения о trust boundary. Для других объектов требуются свои административные identities и права.

При отсутствии административной учётной записи общий preflight завершится `INFRASTRUCTURE_ACCESS_NOT_COMMISSIONED` до любых изменений системы. Следующий шаг — один раз принять инфраструктурную модель прав для runner и провести её приёмку; последующие задачи используют те же credentials без индивидуального staging.

## Приёмка

- Проверено: site/target identity; отсутствие скрытого root-worker у №40.
- Проверить: рабочий Ansible `become` на runner, фиксированный Git SHA, корректный источник секретов (если нужна password-based escalation), отсутствие plaintext в логах.
- Выполнить №40: `INFRASTRUCTURE_ACCESS_READY` → установка ПО → `systemctl active/enabled` → вывод кодa либо `ALREADY_AUTHORIZED`.
- Независимо убедиться `Online` в подключении Remote Desktop Commander и выполнить разрешённый read-only вызов агентом.
- Повторная №40 при неизменном состоянии должна приводить к `changed=0`.
- Ни один из пунктов не даёт полномочий на PVE host или другие guests: каждый managed target авторизуется отдельно.

Для самостоятельной работы агента использовать [единый runbook](ai-agent-execution.md).
