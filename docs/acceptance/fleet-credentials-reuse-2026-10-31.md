# Проверка повторного использования Fleet credentials — ACCEPTED

**Дата:** 2026-10-31  
**Site:** `serzhan-home`, Jonsbo, LXC CT1231  
**Статус:** **ACCEPTED — read-only reuse в новом Ansible process**.

## Проверенный сценарий

После нового Fresh Bootstrap с закреплённым guest checkout `e89d9043ba88607a077e7636d9b0890e65f96811` оператор вручную выполнил в Semaphore:

- №00: `result=PASS`, `created=12`, `unchanged=1`.
- №05: `GITHUB_PRIVATE_FLEET_PASS`.
- №06: `FLEET_VAULT_UNLOCKED`; сохранённый пароль mode `0600`.
- №00 повторно: `result=PASS`, `created=0`, `updated=0`, `unchanged=13`. Исправление сравнения секрета №06 в Semaphore 2.19 доказано живым повтором.

Отдельно оператор запустил новый read-only playbook `playbooks/verify-fleet-credentials.yml` от пользователя `semaphore` из уже существующего доступного checkout Semaphore `/var/lib/semaphore/tmp/project_1/repository_1_template_1`, предварительно проверив SHA `6fef78d3cbd347552267c1bfd14bb0db5a683771`. Код в `/opt/digitalhouses/src` закрыт root-only родительским каталогом, поэтому не использовать этот путь для прямого запуска от `semaphore`. Для наследуемой локали Proxmox необходимо явно установить `LANG=C.UTF-8 LC_ALL=C.UTF-8`.

**Фактический результат последнего read-only запуска:**

```text
result: CREDENTIALS_REUSE_PASS
github_authorization_reused: true
vault_master_password_reused: true
site_credentials_selected: true
administrator_input_required: false
infrastructure_changed: false
PLAY RECAP: runner ok=15 changed=0 unreachable=0 failed=0
```

Это подтверждает, что независимый процесс Ansible без UI-ввода повторно использует авторизацию GitHub и сохранённый master password Vault, а общая роль `fleet_credentials` выбирает credentials текущего site. Секреты, OAuth-токены и расшифрованные значения в логи или публичный документ не выводились.

## Граница приёмки

- **Не проверено:** фактическая перезагрузка CT1231 и reuse после reboot; истечение срока/обновление OAuth-токена; идемпотентный повтор №05/№06; downstream задачи 10–90, PVE worker, удалённые apply-операции.
- №07 существует как playbook в публичном Git, **но не зарегистрирована как template в Semaphore**; заблокированное изменение каталога не обходить.
- Повторное удаление/переустановка CT1231 не требуется и не разрешается этим отчётом.
- Read-only прямой запуск — инженерная проверка; стандарт пользовательского запуска задач остаётся через Semaphore.

## Проверенная команда с PVE

```bash
pct exec 1231 -- runuser -u semaphore -- env \
  LANG=C.UTF-8 LC_ALL=C.UTF-8 bash -c '
set -euo pipefail
SRC=/var/lib/semaphore/tmp/project_1/repository_1_template_1
test -r "$SRC/playbooks/verify-fleet-credentials.yml"
test "$(git -C "$SRC" rev-parse HEAD)" = "6fef78d3cbd347552267c1bfd14bb0db5a683771"
export ANSIBLE_ROLES_PATH="$SRC/roles"
/opt/ansible/venv/bin/ansible-playbook \
  -i /var/lib/ansible/site-data/serzhan-home/hosts.yml \
  "$SRC/playbooks/verify-fleet-credentials.yml"
'
```

Путь к ephemeral checkout и SHA — параметры конкретной приёмки, не универсальная команда для всех site. Из существующих запусков №00/05/06 берите актуальный checkout и проверяйте SHA перед исполнением.
