# Первичная настройка Fleet credentials — Semaphore задачи №05 и №06

**Статус:** реализация в Git есть; живой E2E этих задач на runner ещё не выполнен.  
**Вне области:** изменения Tiny Bootstrap, права PVE, развёртывание Media VM, настройка Cloudflare.

## Ожидаемый операторский путь

1. `00-Обновить список задач` — получить каталог GitHub (без автостарта).
2. `05-Получить credentials GitHub` — посмотреть одноразовый код в журнале Semaphore, открыть `https://github.com/login/device`, подтвердить доступ. Задача ждёт входа, проверяет доступ к приватному `DigitalHouses/dh_ansible_fleet_detail` и клонирует его.
3. `06-Ввести master password Fleet Vault` — в диалоге запуска заполнить одно скрытое поле `Master password Fleet Vault` (тип `secret`). Пароль проверяется расшифровкой `secrets/fleet.vault.yml`, сохраняется однократно в файле `0600`.
4. Дальнейшие задачи вызывают общую роль `fleet_credentials` (только если им нужны secrets). Никакого повторного ввода после reboot не нужно.

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

Пока не изменены соответствующие playbooks/worker, задачи №10–90 не считаются автоматически готовыми после №06: теперь только доступ к Fleet и Vault подготовлен. Дальнейшая интеграция — независимые изменения конкретных задач.

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
