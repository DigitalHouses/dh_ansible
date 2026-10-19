# DigitalHouses · Ansible

Один универсальный набор кода для объектов DigitalHouses. **Дом отличается данными, а не кодом.**

| Репозиторий | Доступ | Назначение |
| --- | --- | --- |
| `DigitalHouses/dh_ansible` | PUBLIC | Ansible, Bootstrap, Semaphore, installer, CI и тесты |
| `DigitalHouses/dh_ansible_fleet_detail` | **PRIVATE** | Декларативные inventory объектов и зашифрованные Vault-файлы |

Секреты, реальные inventory, токены и GitHub credentials не публикуются в коде и не передаются в гостевой Semaphore runner.

## Единая точка входа

`bootstrap/install.sh` входит в `dh_ansible`. Отдельный старый репозиторий installer остаётся временным rollback и не участвует в целевой архитектуре.

Выполнять **только на выбранном PVE**, из-под root, после отдельного разрешения на изменение CT. Пример для синтетического объекта `demo-office` (на действующей площадке используйте её собственный slug):

```bash
clear
dh_install() {
  local REF="649a9016481f525294dcf70c7b94f7500f234f30"
  local SHA="428434a02cac15c0dae3346dc65028089841832a"
  local DEST="/root/dh-ansible-install.sh"
  curl -fsSL --retry 3 \
    "https://raw.githubusercontent.com/DigitalHouses/dh_ansible/$REF/bootstrap/install.sh" \
    -o "$DEST" || return 1
  python3 - "$DEST" "$SHA" <<'PY' || return 2
import hashlib
import pathlib
import sys
raw = pathlib.Path(sys.argv[1]).read_bytes()
digest = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\\0" + raw).hexdigest()
if digest != sys.argv[2]:
    raise SystemExit("BLOCKED: unexpected installer content")
print("INSTALLER_GIT_BLOB=VERIFIED")
PY
  bash -n "$DEST" || return 2
  bash "$DEST" "demo-office"
  local RC=$?
  echo "INSTALLER_RC=$RC"
  return "$RC"
}
dh_install
echo "TERMINAL REMAINS OPEN"
```

Slug задаётся в аргументе или вводится интерактивно. **Для обоих способов обязательно подтверждение `y/n` до OAuth, чтения Vault и любых изменений PVE**. После подтверждения slug передаётся как `--site` в pinned Bootstrap, который проверяет идентичность с PRIVATE Fleet inventory. Существующий CT не удаляется и не перезаписывается без отдельного разрешения; допустимые режимы продолжения: `--resume`, `--resume-fresh`, `--resume-ansible`, `--resume-semaphore`.

В установщике закреплены SHA публичного Bootstrap, SHA его Git blob, версия/checksum GitHub CLI и независимо закреплённый Fleet commit. Ввод Vault master остаётся только на PVE. Отказ или неверный slug блокируют установку.

## Semaphore · локальные операции

При установке создаются три шаблона, привязанные к site-local inventory:

- `Проверка запуска Ansible` — read-only smoke.
- `Аудит локального runner` — read-only аудит identity, systemd, Git и Ansible.
- `Установить Remote Desktop Commander` — **только по запросу оператора**, не запускается автоматически.

У Semaphore нет общих root-прав. Для Remote Commander на runner заранее подготавливается root-owned фиксированный `systemd.path` / `oneshot` механизм. При запуске шаблон размещает только локальный запрос `INSTALL`; root выполняет исключительно закреплённый playbook установки Remote Commander. Все PVE/TrueNAS операции и доступ к Fleet Vault недоступны через этот механизм.

## Приёмка

19 октября 2026: **Fresh Bootstrap E2E домашнего pilot runner принят** — официальный Debian 13 с нуля, HTTPS Semaphore 2.19.16, Ansible Core 2.21.4, два UI smoke-аудита SUCCESS и read-only повторный seeder PASS. Это **не** разрешение на инфраструктурные write-операции или массовое развёртывание.

Новый flow подтверждения slug и on-demand Remote Commander проверяется отдельно CI и контролируемым `--resume-semaphore` на действующем тестовом CT; повторный destructive E2E для их применения не нужен. Production объекты не менять без отдельной приёмки.
