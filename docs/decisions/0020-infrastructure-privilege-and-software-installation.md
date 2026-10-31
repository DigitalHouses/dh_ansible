# ADR-0020 — Общие полномочия инфраструктурного Ansible и установка ПО

**Статус:** единая модель доступа к Linux принята; исходники локального sudo и Fleet inventory в Git, **живой retrofit CT1231 и полный E2E ещё не выполнены**.  
**Дата:** 2026-10-31.  
**Причина:** задача №40 выявила антипаттерн «отдельный root-owned systemd watcher + staging + handoff под каждый install playbook». Владелец отверг этот подход. Аналогичные операции будут у множества задач.

## Решение

Установка ПО и управление системными сервисами — **тип административной операции над целевым узлом**, а не привилегия конкретного номера Semaphore. Для всех таких задач используется **обычный Ansible** (роли/modules) с общей моделью `become` и явно commissioned credentials. Никакого отдельного per-task privileged worker, request-file, unit, staging или sudo-исключения под номер задачи.

- **Control plane:** `semaphore` запускает Ansible; до дальнейшей изоляции master Fleet Vault и private Git всё ещё хранятся локально согласно принятой №05/06. Это известный риск для всего Fleet.
- **Managed node:** локальный runner LXC или иной Linux-host, отдельно идентифицированный и принятый под управление.
- **Особое утверждённое правило для собственного disposable runner LXC:** пользователь `semaphore` получает `sudo NOPASSWD: ALL` **внутри этого LXC** без дополнительного пароля root. Это полноценный root, а не ограниченное `software_manage`. На других nodes это правило не распространяется.
- **Administrative execution identity:** утверждённая для конкретного managed node учётная запись/механизм `sudo` + `ansible_become`; на удалённых хостах — pinned SSH host key + отдельная учётная запись, на API-сервисах — собственный scoped API principal. Секреты и правила доступа — в encrypted Fleet SoT и локальном commissioned credential store, не в публичном Git.
- **Shared preflight:** роль `infrastructure_access` подтверждает действительную способность `become` на целевом Linux узле; она ничего не устанавливает и не выдаёт дополнительных прав.
- **Application installation:** обычная `include_role`/Ansible modules (`apt`, `package`, `template`, `systemd_service`, `get_url`, и т.д.) с `become: true` только там, где он нужен.
- **Authorization:** права Linux/API сами по себе **не заменяют** разрешение владельца на изменение/удаление, а согласование операции не подменяет реальные ОС-права.

## Важная граница безопасности

`sudo` с полноценным `become` позволяет выполнять root-команды на целевом узле. Пометка `software_manage` **не является** ограничением на уровне ОС. Исключение `NOPASSWD: ALL` одобрено для собственного disposable runner LXC как осознанный отказ от бесполезного второго пароля; на PVE, удалённые Linux hosts и production guests это исключение не распространяется.

Поэтому commissioning административного доступа — **явное инфраструктурное решение на уровне site+target**, не скрытый side effect №00 или №40. Для runner он выполняется стандартным Bootstrap, а для существующего runner — один раз из уже авторизованной guest-root сессии через `playbooks/commission-runner-local-admin.yml`. Сначала выбираются и проверяются учётная запись, способ аутентификации, доверенный код/revision, ограничения окружения и достаточность прав. На production запрещено неконтролируемое `sudo ALL` через изменяемый `main`; если работа выполняется с root-правами, код должен быть из проверенной зафиксированной ревизии, а операция согласована по правилам риска.

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

- **Tiny Bootstrap**: создаёт site runner, устанавливает `sudo` и локальное правило для `semaphore`, затем Semaphore и №00. Не создаёт root-workers каждой задачи и не выдаёт доступ на PVE. Это изменение исходников после предыдущего принятого Fresh E2E, а не новое доказательство живой установки.
- **№00**: только каталог Semaphore; не устанавливает sudo, root-worker, систему исполнения из новой ревизии и не изменяет права ОС.
- **№05/06**: предоставляют GitHub/Fleet Vault secrets, но не создают административного Linux principal и не включают `become`.
- **Инфраструктурный commissioning административного доступа**: для fresh runner — встроен в Bootstrap; для существующего CT1231 — одна root-инициированная роль `runner_local_admin` без удаления контейнера. Для **других** Linux/PVE targets применяется своя identity, не привязанная к №40.
- **Existing CT1231**: предыдущий Fresh Bootstrap был установлен до этой модели. На нём `sudo/become` ещё не принят живым тестом. Требуется однократный штатный retrofit `runner_local_admin` через разрешённую guest-root сессию; обновление №00 само по себе root не выдаёт. До retrofit №40 возвращает `INFRASTRUCTURE_ACCESS_NOT_COMMISSIONED`.

## Критерии приёмки

1. Независимый read-only preflight определяет commissioned / not commissioned `become` на конкретном runner, не меняя узел.
2. Установочный playbook использует `become`, отсутствует task-specific root watcher и не выдаются скрытые права.
3. Один и тот же механизм применяется минимум двумя различными software/service tasks без нового root-helper.
4. Управляемая установка — idempotent (`changed=0` на повторном запуске), с независимой проверкой действительного состояния службы.
5. Ошибка credentials/root предсказуемо даёт BLOCKED, без установки, без стирания данных и без ложного PASS.
6. Новый runner и существующий runner имеют документированный **один общий** путь commissioning привилегий, не зависящий от названия приложения.

**Не утверждать LIVE PASS** до успешного commissioning и настоящего запуска.
