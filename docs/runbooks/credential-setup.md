# Первичная настройка Fleet credentials — Semaphore задачи №05 и №06

> **Новые объекты (после принятия `site_vault_v1`):** пользоваться
> [общим site credentials onboarding](site-credentials-onboarding.md).
> №05 по-прежнему выполняет первичную авторизацию private Git,
> а №06 проверяет и сохраняет **пароль отдельного Vault данного объекта**.
> Название скрытого поля `fleet_master_password` в UI пока сохранено для
> совместимости исторического шаблона и не означает общий пароль для новых
> домов. Приведённые ниже логи, пути и результаты `fleet-vault-password`
> относятся к старому профилю `fleet_legacy_v1`; не переносить его на
> Fresh `serzhan-lab` или другие новые runner.


**Статус:** **Fresh E2E ACCEPTED 2026-10-31** — №00, №05 и №06 успешно выполнены оператором в Semaphore на чистом CT1231. [Отчёт и границы приёмки](../acceptance/fleet-credentials-2026-10-31.md).  
**Вне области:** изменения Tiny Bootstrap, права PVE, развёртывание Media VM, настройка Cloudflare.

## Ожидаемый операторский путь

1. `00-Обновить список задач` — получить каталог GitHub (без автостарта).
2. `05-Получить credentials GitHub` — посмотреть одноразовый код в журнале Semaphore, открыть `https://github.com/login/device`, подтвердить доступ. Задача ждёт входа, проверяет доступ к приватному `DigitalHouses/dh_ansible_fleet_detail` и клонирует его.
3. `06-Ввести master password Fleet Vault` — в диалоге запуска заполнить одно скрытое поле `Master password Fleet Vault` (тип `secret`). Пароль проверяется расшифровкой `secrets/fleet.vault.yml`, сохраняется однократно в файле `0600`.
4. Дальнейшие задачи вызывают общую роль `fleet_credentials` (только если им нужны secrets). Пароль сохраняется в локальном файле. **Реальный reboot-test CT1231 завершился PASS 2026-10-31**: GitHub и Vault credentials повторно использованы без ввода.

Результаты без чувствительных значений: `GITHUB_PRIVATE_FLEET_PASS`, `FLEET_VAULT_UNLOCKED`.

**Минимальный локальный формат:**

```text
/var/lib/semaphore/.dh-credentials/           semaphore:semaphore 0700
  gh/                                        GitHub CLI credential store
  fleet/                                     приватный Git checkout
  fleet-vault-password                       semaphore:semaphore 0600
```

Ни мастер-пароль, ни OAuth token не должны попадать в stdout/записи Ansible. Это компромиссная модель хранения для пилота: Secrets сохраняются на runner в локальных файлах; дополнительные сервисы и split-secret architecture не вводятся.

## Только чистое развёртывание

Установка и проверка новой ревизии №05/06 **всегда** выполняются через Fresh E2E:

1. Проверить, что выполняемся на утверждённом Jonsbo, а CT1231 имеет точно ожидаемые hostname, IP, unprivileged rootfs и не содержит сторонних mount points.
2. По **явному адресному разрешению** владельца удалить только идентифицированный тестовый CT1231 с его rootfs; не затрагивать HAOS VM2253 и остальные гости.
3. Запустить **одну** pinned команду `bootstrap/install.sh` из [README](../../README.md). Она создаёт новый Debian 13 LXC, ставит Ansible/Semaphore и регистрирует только №00.
4. В Semaphore вручную запустить №00. Она загрузит №05, №06 и другие шаблоны из публичного GitHub.
5. Запустить №05, подтвердить код в браузере, затем №06 со скрытым вводом master password. Проверить `GITHUB_PRIVATE_FLEET_PASS` и `FLEET_VAULT_UNLOCKED`.

**Никаких ручных обновлений seeder, пересылки файлов в LXC, `git pull` на действующем runner, `--resume` или локальных hotfix** в тестовом цикле. Изменения сначала в GitHub, новый immutable pin, затем полная установка одной командой. Не удалять CT1231 повторно без нового разрешения.

## Использование в playbooks

После №05 и №06 обычная задача Ansible, которой нужны credentials, включает:

```yaml
- name: Read this site's managed credentials
  ansible.builtin.include_role:
    name: fleet_credentials
```

Роль выбирает `fleet_secrets.sites[site.id]` и формирует `fleet_site_credentials`. В журнале сам секрет не печатается. Это **не заменяет** разрешение на инфраструктурный apply, установку PVE workers или проверку доступа на целевом сервере.

После принятой №06 подготовлены доступ к Fleet и master password, **но задачи №10–90 не становятся автоматически рабочими**: соответствующие playbooks ещё должны подключить `fleet_credentials`, а отсутствующие PVE/Cloudflare workers требуют отдельной установки и проверки. Повтор №05/06 остаётся непроверенным. **Повторное использование после перезагрузки прошло живую приёмку**, см. [Reboot E2E](../acceptance/fleet-credentials-reuse-2026-10-31.md).

## Принятый read-only тест повторного использования (2026-10-31)

Повторная №00 после №05/06 завершилась `created=0 updated=0 unchanged=13 failed=0`: исправление нормализации отсутствующего `values` у поля `secret` в Semaphore 2.19 принято живым тестом.

Независимый запуск `playbooks/verify-fleet-credentials.yml` с сохранёнными GitHub и Vault credentials **успешен**: `CREDENTIALS_REUSE_PASS`, `ok=15 changed=0 failed=0`, повторный ввод не требовался. [Отчёт и проверенная команда](../acceptance/fleet-credentials-reuse-2026-10-31.md).

Для прямого инженерного запуска от `semaphore`:

- Задавать `LANG=C.UTF-8 LC_ALL=C.UTF-8` во избежание наследования неподдерживаемой Proxmox locale.
- Использовать проверенный доступный пользователю `semaphore` checkout с точным Git SHA и `ANSIBLE_ROLES_PATH=<checkout>/roles`; путь `/opt/digitalhouses/src` недоступен `semaphore` через root-owned родительский каталог `/opt/digitalhouses` с mode `0700`.
- Не копировать playbook вручную в LXC и не изменять права Bootstrap ради прямого запуска.
- Это разовая инженерная проверка, а не зарегистрированная задача №07 в Semaphore. Изменение каталога №07 ранее было отклонено; не считать её установленной.
- **Реальный reboot-test PASS 2026-10-31:** после `pct reboot 1231` получен `CREDENTIALS_REUSE_PASS`, `ok=15 changed=0 failed=0`, без повторного ввода. [Приёмка](../acceptance/fleet-credentials-reuse-2026-10-31.md).

## Проверка после установки

Не считывая чувствительные значения:

```bash
pct exec 1231 -- bash -c '
  test -d /var/lib/semaphore/.dh-credentials/fleet/.git
  test "$(stat -c %a /var/lib/semaphore/.dh-credentials/fleet-vault-password)" = 600
  test "$(stat -c %U /var/lib/semaphore/.dh-credentials/fleet-vault-password)" = semaphore
  echo FLEET_CREDENTIAL_FILES=PASS
'
```

Никаких новых фоновых служб, провайдеров, root-worker или переключения версии Bootstrap для этого сценария не создаётся.
