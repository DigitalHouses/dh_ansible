# ADR-0020 — Общие полномочия инфраструктурного Ansible и установка ПО

**Статус:** архитектурное решение принято к реализации, **доступы на работающем CT1231 ещё не commissioned**.  
**Дата:** 2026-10-31.  
**Причина:** задача №40 выявила антипаттерн «отдельный root-owned systemd watcher + staging + handoff под каждый install playbook». Владелец отверг этот подход. Аналогичные операции будут у множества задач.

## Решение

Установка ПО и управление системными сервисами — **тип административной операции над целевым узлом**, а не привилегия конкретного номера Semaphore. Для всех таких задач используется **обычный Ansible** (роли/modules) с общей моделью `become` и явно commissioned credentials. Никакого отдельного per-task privileged worker, request-file, unit, staging или sudo-исключения под номер задачи.

- **Control plane:** `semaphore` запускает Ansible и хранит site-scoped Fleet credentials согласно уже принятой модели №05/06.
- **Managed node:** локальный runner LXC или иной Linux-host, отдельно идентифицированный и принятый под управление.
- **Administrative execution identity:** утверждённая для конкретного managed node учётная запись/механизм `sudo` + `ansible_become`; на удалённых хостах — pinned SSH host key + отдельная учётная запись, на API-сервисах — собственный scoped API principal. Секреты и правила доступа — в encrypted Fleet SoT и локальном commissioned credential store, не в публичном Git.
- **Shared preflight:** роль `infrastructure_access` подтверждает действительную способность `become` на целевом Linux узле; она ничего не устанавливает и не выдаёт дополнительных прав.
- **Application installation:** обычная `include_role`/Ansible modules (`apt`, `package`, `template`, `systemd_service`, `get_url`, и т.д.) с `become: true` только там, где он нужен.
- **Authorization:** права Linux/API сами по себе **не заменяют** разрешение владельца на изменение/удаление, а согласование операции не подменяет реальные ОС-права.

## Важная граница безопасности

`sudo` с полноценным `become` позволяет фактически выполнять root-команды на целевом узле. Пометка `software_manage` в задаче **не является** ограничением на уровне ОС, и разрешение `NOPASSWD: ALL` для любого публичного playbook не должно выдаваться автоматически.

Поэтому commissioning административного доступа — **явное инфраструктурное решение на уровне site+target**, не скрытый side effect №00 или №40. Сначала выбираются и проверяются учётная запись, способ аутентификации, доверенный код/revision, ограничения окружения и достаточность прав. На production запрещено неконтролируемое `sudo ALL` через изменяемый `main`; если работа выполняется с root-правами, код должен быть из проверенной зафиксированной ревизии, а операция согласована по правилам риска.

Не записывать пароли в extra vars CLI, environment log, публичный inventory, Ansible stdout. Использовать `no_log` и штатный Vault. Не выдавать `semaphore` доступ к PVE host root только ради локальной установки в CT.

## Классификация возможностей каждой задачи

Для всех **новых и изменяемых** Semaphore tasks указывать:

| Поле | Назначение |
| --- | --- |
| `target` | Где реально выполняется работа: runner LXC, guest VM, PVE host, appliance, API |
| `operations` | `read`, `software_manage`, `service_manage`, `filesystem_manage`, `host_manage`, `destructive` |
| `identity` | Какой учётной записью/connection/become/API работает Ansible |
| `credential_source` | Локальный bootstrap identity или конкретное namespace из Fleet Vault |
| `approval` | Какой риск и какие решения владельца обязательны для данного apply |
| `postconditions` | Что проверяем после apply, включая idempotency |
| `privilege_status` | `COMMISSIONED` / `NOT_COMMISSIONED`; если второй — BLOCKED без изменений |

Эти поля — **архитектурный и ревью-контракт**, а не новый динамический DSL или самостоятельно выдаваемые ОС-права. Не вводить несколько механизмов работы с одним типом доступа.

## Установка Node.js и Remote Desktop Commander (№40 как пример потребителя)

Задача №40 использует `infrastructure_access` с `software_manage` и общую существующую роль `runner_remote_commander` с `become: true`. Она не устанавливает «обработчик №40», не меняет sudoers и не получает исключительных полномочий.

Другие задачи установки ПО на runner/VM используют **тот же интерфейс**. Для PVE host полномочия вводятся **отдельно для PVE**, не наследуются автоматически от root внутри LXC.

## Разделение bootstrap, №00 и commissioning

- **Tiny Bootstrap**: создаёт site runner и Semaphore, не предустанавливает root-workers каждой задачи; уже принятый четырёхэтапный контракт.
- **№00**: только каталог Semaphore; не устанавливает sudo, root-worker, систему исполнения из новой ревизии и не изменяет права ОС.
- **№05/06**: предоставляют GitHub/Fleet Vault secrets, но не создают административного Linux principal и не включают `become`.
- **Инфраструктурный commissioning административного доступа**: отдельный, **общий** для данного target шаг после идентификации и явного решения об объёме root-доступа. Он не привязан к №40 и переиспользуется всеми задачами с `become`.
- **Existing CT1231**: после ранее принятого Fresh Bootstrap на нём не commissioned `sudo/become` для `semaphore`. Поэтому обновление только №00 не сделает №40 устанавливающей ПО задачей. На настоящий момент №40 должна честно возвращать `INFRASTRUCTURE_ACCESS_NOT_COMMISSIONED`.

## Критерии приёмки

1. Независимый read-only preflight определяет commissioned / not commissioned `become` на конкретном runner, не меняя узел.
2. Установочный playbook использует `become`, отсутствует task-specific root watcher и не выдаются скрытые права.
3. Один и тот же механизм применяется минимум двумя различными software/service tasks без нового root-helper.
4. Управляемая установка — idempotent (`changed=0` на повторном запуске), с независимой проверкой действительного состояния службы.
5. Ошибка credentials/root предсказуемо даёт BLOCKED, без установки, без стирания данных и без ложного PASS.
6. Новый runner и существующий runner имеют документированный **один общий** путь commissioning привилегий, не зависящий от названия приложения.

**Не утверждать LIVE PASS** до успешного commissioning и настоящего запуска.
