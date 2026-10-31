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

## Однократный переход текущего принятого CT1231

Проверенный 31.10.2026 Tiny Bootstrap закрепил старую версию `bootstrap/semaphore-project.py`, где проверка каталога поддерживала только `enum` survey. Поэтому без однократного обновления installed seeder новый `secret` survey задачи №06 не пройдёт валидацию при запуске №00.

На утверждённом PVE, в root shell, **после read-only сверки CT1231** и при отсутствии работающей задачи №00:

```bash
pct config 1231 | grep -Fx 'hostname: ansible-serzhan-home'
pct config 1231 | grep -Fx 'unprivileged: 1'
pct exec 1231 -- bash -euc '
  test "$(hostname)" = "ansible-serzhan-home"
  ! systemctl is-active --quiet dh-semaphore-catalog-sync.service
  cd /opt/digitalhouses/src
  git fetch --depth=1 origin e1499370833a469799501b12441eac81392671d2
  tmp="$(mktemp /root/dh-catalog-seeder.XXXXXXXX.py)"
  trap '\''rm -f "$tmp"'\'' EXIT
  git show e1499370833a469799501b12441eac81392671d2:bootstrap/semaphore-project.py > "$tmp"
  python3 -m py_compile "$tmp"
  install -o root -g root -m 0644 "$tmp" /opt/digitalhouses/bootstrap/semaphore-project.py
  echo "CATALOG_SEEDER_UPDATED=PASS"
'
```

Это **не** новый Bootstrap, не recovery, не обновление Media/PVE workers. Обновляется один фиксированный root-owned Python-файл, который использует существующая задача №00. Затем оператор запускает №00 повторно из Semaphore. Она должна добавить две новые задачи №05 и №06, сохранив исходную №00 и остальные шаблоны.

Не показывать в чате пароль Vault, GitHub OAuth token или содержимое приватного репозитория. При сбое `05` или `06` проверить журнал соответствующей задачи и исправить только фактический блокер, не уничтожая runner.

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
