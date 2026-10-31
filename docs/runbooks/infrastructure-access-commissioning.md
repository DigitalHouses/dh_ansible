# Общий commissioning административного доступа Ansible

**Назначение:** один инфраструктурный credential/privilege contract на `site + managed target`, используемый **всеми** Ansible-задачами, которым нужно устанавливать ПО, менять конфигурацию OS или управлять сервисами. Не создавать отдельный обработчик/credential под №40, №60, №80 или конкретное приложение.

**Стандарт:** [ADR-0020](../decisions/0020-infrastructure-privilege-and-software-installation.md). **Реестр всех задач:** [матрица полномочий](../standards/task-privilege-matrix.md).

## Сначала установить, на каком узле нужны права

- **Runner LXC:** установка приложений/Node.js, создание `systemd` units, обновление пакетов, проверка служебных файлов внутри runner. Нужен Linux `become` **только в LXC**.
- **Другая Linux VM/LXC:** отдельные target user/SSH host key и `become` на этом guest. Root на runner не даёт прав внутри guest.
- **Proxmox host:** отдельный управляемый API/SSH principal и approval на операции PVE. Root в runner не равен root PVE.
- **Cloudflare/HAOS/апплаенс:** отдельные token/certificate/permission scope соответствующего API, не Linux `sudo`.

## Утверждённое упрощение для собственного runner LXC

Служебный runner не получает отдельный пароль root: локальный `semaphore` имеет
`sudo NOPASSWD: ALL` только **в своём LXC**. Это сознательно полный root внутри
расходного контейнера. На PVE, HAOS, другие VM/LXC и Cloudflare это право не
распространяется.

- Новый runner: `bootstrap/semaphore-guest-install.py` устанавливает `sudo`,
  проверяет sudoers через `visudo`, а unit Semaphore больше не блокирует `become`
  через `NoNewPrivileges`, `ProtectSystem` и `ProtectHome`.
- Существующий runner: root **внутри уже созданного LXC** выполняет единый
  `playbooks/commission-runner-local-admin.yml` из проверенной ревизии Git.
  Роль `runner_local_admin` идемпотентно ставит `sudo`, меняет systemd unit,
  перезапускает Semaphore и проверяет фактический root UID от `semaphore`.
  Она не переустанавливает Semaphore, не изменяет Fleet credentials и не
  подключается к PVE.
- Задачи `software_manage`, `service_manage` и `filesystem_manage` вызывают
  обычный `infrastructure_access` → `become: true`. Для нового приложения
  не требуется ни пароль, ни отдельный административный worker.
- Уже работающий CT1231 нельзя удалять или пересоздавать для commissioning.
  Retrofit и №40 пока не подтверждены live E2E.

**Важно:** постоянный master Fleet Vault и GitHub credential от №05/06
по-прежнему находятся у `semaphore`; это значительно более существенный
риск, чем root внутри runner. Уменьшение области глобальных секретов —
следующий отдельный этап. В рамках текущего упрощения credentials не
изменяем и не переносим.

## Общая доставка данных своего site

После №05 приватный checkout находится в
`/var/lib/semaphore/.dh-credentials/fleet/`. Роль `site_inventory_prepare`
выполняет только одну операцию: проверяет `site.id`, существование нужных
`inventories/<site>/*.yml` в этом checkout, затем через native Ansible
`become` идемпотентно размещает **только запрошенные файлы**
в `/var/lib/ansible/site-data/<site>/` как `root:semaphore 0640`.
Никакого `dh-site-data.path` или root-worker. Источник на этом этапе —
последняя локально скачанная/обновлённая №05 копия Fleet; свежесть
удалённого Git без №05 не предполагается.

## Общий порядок, один раз на каждый target

1. Read-only audit: `site.id`, target identity, существующие accounts/group, способ SSH/local connection и `sudo`/become, ограничения host keys/TLS, доступ к pin Git SHA.
2. Выбрать **административную execution identity** и обосновать её разрешения. Для disposable site runner решение о полном локальном sudo уже принято выше. Для других managed nodes `NOPASSWD: ALL` не является автоматически разрешённым.
3. Для парольного escalation хранить пароль как **site/target secret в encrypted Fleet Vault**, а не в `catalog`, shell/history, CLI args, исходниках или системных логах. Воспользоваться уже принятой №05/06 моделью доступа к Fleet; не выдумывать имя поля, пока не утверждена схема приватного inventory/Vault. Параметр `ansible_become_password` предоставлять Ansible безопасно с `no_log` на задачах, работающих с секретами.
4. Явно разрешённое, контролируемое изменение identity/sudo на **указанном target** (переиспользуемая инфраструктурная настройка, не установка Remote Commander). На действующих узлах провести audit → plan → apply → verify; не использовать №00 для привилегированного изменения прав.
5. После commissioning запустить *read-only* роль `infrastructure_access` из непривилегированного Semaphore runner; требуемый результат `INFRASTRUCTURE_ACCESS_READY`, effective UID 0. При отказе должна возвращаться `INFRASTRUCTURE_ACCESS_NOT_COMMISSIONED` до любых writes.
6. Использовать этот же доступ во всех Ansible roles, применяя `become: true` **только для привилегированных действий**. Не создавать другой privileged worker для второго приложения на том же target.
7. Reboot/reuse и независимые postconditions; проверить права на секреты, отсутствие их в логах и невозможность получить доступ к постороннему target. Повторный неизменный apply = `changed=0`.

## Упрощённый интерфейс для playbooks

```yaml
- name: Verify infrastructure administrative access
  ansible.builtin.include_role:
    name: infrastructure_access
  vars:
    infrastructure_access_capability: software_manage

- name: Install application using the common administrative identity
  ansible.builtin.include_role:
    name: example_application
    apply:
      become: true
```

Уровень доступа на Linux проверяет **реальная** `sudo/become` identity и настройки ОС. Маркер `infrastructure_access_capability` — аудитная классификация, не самостоятельная система RBAC или sandbox.

## Текущий пилот serzhan-home

Проверены: Bootstrap, №00, №05/06, reuse Fleet credentials после reboot. **Не проверен и не создан** общий Linux administrative principal/`become` для `semaphore` внутри CT1231. Это отдельная задача подключения административных прав к уже работающему runner; её нельзя реализовать обычным `git pull` или №00, потому что `semaphore` непривилегирован. Выдавать дополнительные root-права только после утверждения identity, источника credentials и модели доверия к исполняемому Git checkout.

**Статус:** контракт и read-only preflight реализованы в public repo; actual target commissioning и live acceptance — PENDING.
