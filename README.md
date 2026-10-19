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
  local REF="f5a9c5809f2fbc135aa342eabef8a0ebe828c0d2"
  local SHA="db0962575999201c5460c46e7a882c1c537b4417"
  local DEST="/root/dh-ansible-install.sh"
  curl -fsSL --retry 3 \
    "https://raw.githubusercontent.com/DigitalHouses/dh_ansible/$REF/bootstrap/install.sh" \
    -o "$DEST" || return 1
  python3 - "$DEST" "$SHA" <<'PY' || return 2
import hashlib
import pathlib
import sys
raw = pathlib.Path(sys.argv[1]).read_bytes()
digest = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
if digest != sys.argv[2]:
    raise SystemExit("BLOCKED: unexpected installer content")
print("INSTALLER_GIT_BLOB=VERIFIED")
PY
  bash -n "$DEST" || return 2
  bash "$DEST"
  local RC=$?
  echo "INSTALLER_RC=$RC"
  return "$RC"
}
dh_install
echo "TERMINAL REMAINS OPEN"
```

При каждом запуске установщик **обязательно запрашивает Site ID интерактивно** (например, `serzhan-home`) и предлагает подтвердить `y/n`. `n` возвращает к повторному вводу Site ID; некорректный формат также позволяет повторить ввод. `y` фиксирует окончательный ID, который передаётся как `--site` в pinned Bootstrap для проверки с PRIVATE Fleet inventory. Ввод через аргумент отключён во избежание случайного выбора неверного объекта. EOF/Ctrl+D до подтверждения блокирует установку; Ctrl+C прерывает её. **Подтверждение происходит до OAuth, чтения Vault и любых изменений PVE.** Существующий CT не удаляется и не перезаписывается без отдельного разрешения; допустимые режимы продолжения: `--resume`, `--resume-fresh`, `--resume-ansible`, `--resume-semaphore`.

В установщике закреплены SHA публичного Bootstrap, SHA его Git blob, версия/checksum GitHub CLI и независимо закреплённый Fleet commit. Ввод Vault master остаётся только на PVE. Отказ или неверный slug блокируют установку.

## Semaphore · локальные операции

При установке создаются три шаблона, привязанные к site-local inventory:

- `10-Проверка запуска Ansible` — read-only smoke.
- `20-Аудит локального runner` — read-only аудит identity, systemd, Git и Ansible.
- `30-Установить Remote Desktop Commander` — **только по запросу оператора**, не запускается автоматически.

Стандарт имён **всех Semaphore-шаблонов** — `10-Название`, `20-Название`, `30-Название`: единая сквозная нумерация, шаг 10 для вставки новых задач (`15-Название`). Сортировка Semaphore по имени отражает эту очередность. Номера не создают автоматических зависимостей и не требуют запуска всех задач подряд. Seeder через официальный API переименовывает только проверенные исходные имена без номера с сохранением `template_id` и истории Task; при неизвестном имени, дублях или drift блокируется.

У Semaphore нет общих root-прав. Для Remote Commander на runner заранее подготавливается root-owned фиксированный `systemd.path` / `oneshot` механизм. При запуске шаблон размещает только локальный запрос `INSTALL`; root выполняет исключительно закреплённый playbook установки Remote Commander. Все PVE/TrueNAS операции и доступ к Fleet Vault недоступны через этот механизм.

## Приёмка

19 октября 2026: **Fresh Bootstrap E2E домашнего pilot runner принят** — официальный Debian 13 с нуля, HTTPS Semaphore 2.19.16, Ansible Core 2.21.4, два UI smoke-аудита SUCCESS и read-only повторный seeder PASS. Это **не** разрешение на инфраструктурные write-операции или массовое развёртывание.

Новый flow подтверждения slug и on-demand Remote Commander проверяется отдельно CI и контролируемым `--resume-semaphore` на действующем тестовом CT; повторный destructive E2E для их применения не нужен. Production объекты не менять без отдельной приёмки.

## Имя локального runner

Hostname LXC: `ansible-<owner_slug>` (например, `ansible-serzhan`). Не путать с `site.id` (`serzhan-home`): он остаётся полным ключом Fleet, Semaphore inventory и Vault. CTID/IP по прежнему стандарту 1231 / .231. Bootstrap сверяет owner_slug из PRIVATE site metadata до создания гостя. Переход на новое имя применяется только при следующем Fresh E2E, старые работающие CT не переименовываются автоматически.
