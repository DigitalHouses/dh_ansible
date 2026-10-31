# DigitalHouses · Ansible

Один универсальный набор кода для объектов DigitalHouses. **Дом отличается данными, а не кодом.**

| Репозиторий | Доступ | Назначение |
| --- | --- | --- |
| `DigitalHouses/dh_ansible` | PUBLIC | Ansible, Bootstrap, Semaphore, installer, CI и тесты |
| `DigitalHouses/dh_ansible_fleet_detail` | **PRIVATE** | Декларативные inventory объектов и зашифрованные Vault-файлы |

Секреты, реальные inventory, токены и GitHub credentials не публикуются в коде и не передаются в гостевой Semaphore runner.

**Обязательный сетевой стандарт:** все новые сетевые VM/LXC, создаваемые Ansible, должны публиковать своё имя `<hostname>.local` через Bonjour/mDNS (`avahi-daemon` для Linux без штатного mDNS; на других ОС — встроенный механизм) и проходить проверку обнаружения с другого узла LAN. Применяется ко всему Fleet, не только к медиасерверу. Правила безопасности, исключений и приёмки: [guest-network-mdns.md](docs/standards/guest-network-mdns.md).

**Обязательное именование ZFS-хранилищ:** вновь создаваемые Ansible пулы данных имеют имя `<owner>-pool` (например, `serzhan-pool`, где `owner` — `site.owner_slug` из Fleet). Существующие пулы при `keep + import` не переименовываются. [Стандарт именования ZFS-пулов](docs/standards/storage-pool-naming.md).

## AI-агентам: как самостоятельно запускать и проверять задачи

**После первого запуска №00 через Semaphore** остальные задачи создаются из GitHub-каталога. Все изменения infrastructure — только через утверждённые Semaphore tasks/Ansible и их согласованные approval. Для операций AI-агентов обязательно читать [операционный runbook](docs/runbooks/ai-agent-execution.md) и [AGENTS.md](AGENTS.md). Общий task runner и privileged workers могут появиться только как отдельная задача, **не** как часть Tiny Bootstrap.

## Единая точка входа · Tiny Bootstrap

Стандартная установка с чистого PVE: **одна команда** под `root` на выбранном Proxmox VE.

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/DigitalHouses/dh_ansible/261645fb4ab49bad8c0b115a49e88db7a47029fe/bootstrap/install.sh)
```

Установщик запрашивает и подтверждает Site ID, затем выполняет ровно четыре этапа:

1. Получить официальный шаблон Debian 13, создать **только отсутствующий** непривилегированный LXC (`CT1231`, адрес последнего октета `.231/24` из `vmbr0`).
2. Клонировать публичный `DigitalHouses/dh_ansible` **внутри нового LXC** и проверить закреплённый Git SHA.
3. Запустить внутри гостя существующий установщик Ansible + Semaphore (с TLS, mDNS, systemd, закрытым файлом начального пароля).
4. Подготовить минимальный локальный site inventory и через HTTPS API Semaphore создать **только** задачу `00-Обновить список задач` с ограниченным on-demand watcher.

Bootstrap **не** устанавливает Ansible на PVE, не требует GitHub OAuth и Fleet master password, не трогает private Fleet credentials, не создаёт Media workers или PVE tokens. Не создаёт никаких других Semaphore tasks и **не запускает №00 сам**. Только после открытия Semaphore оператор запускает №00 для получения полного актуального каталога из GitHub.

**Fresh only:** существующие CT/VM с ID 1231 не принимаются и не удаляются автоматически. Режимов `--resume` нет. При E2E на Jonsbo после неуспеха оператор отдельно удаляет только идентифицированный тестовый CT по [runbook](docs/runbooks/ai-agent-execution.md), сохраняет HAOS VM2253 и запускает ту же команду с нуля. Успешный код возврата, единственная №00 и отдельный успешный запуск №00 — обязательная приёмка.

Начальный адрес Semaphore: `https://<runner-ip>:3000/`. Логин: `admin`. Пароль хранится только внутри LXC: `pct exec 1231 -- cat /root/dh-semaphore-initial-admin-password`; не публиковать пароль в чат. Логи установщика на PVE: `/var/log/digitalhouses/ansible/tiny-*.log` (`root:root 0600`).

## PVE Access · принятый E2E

**Историческая приёмка на прежнем PVE (20 октября 2026):** PVE Access и зашифрованный Fleet Vault были приняты для `serzhan-home`. Это **не** приёмка нового Jonsbo, а Tiny Bootstrap намеренно не переносит старые credentials.
Канонические пользователи `ansible-audit@pve`, `ansible-writer@pve` и Linux `ansible-linux` созданы на PVE.
Независимо подтверждены SSH/sudo, оба API-токена (только GET), совпадение SSH/TLS pin и восстановление
двух токенов с исходным защищённым escrow из зашифрованного Fleet Vault в режиме read-only.
Первый выпуск токенов повторно не выполнялся. Заключительный destructive Fresh E2E CT1231,
включая Semaphore-шаблон `40`, **ещё не выполнен**.

## Semaphore · локальные операции

**Обязательный стандарт проектирования и выбора механизма задач:** [Semaphore · создание задач, reusable-этапы и Workflows](docs/standards/semaphore-task-design.md). Перед добавлением нового шаблона сверяйте функциональный класс, источник site data/секретов, границу root-worker и E2E-приёмку одной кнопкой.


**Новый режим (кандидат; Fresh E2E ещё не принят):** Installer создаёт в Semaphore
**ровно одну задачу — `00-Обновить список задач`**. Остальные определения
хранятся в `catalog/semaphore-tasks.json` публичного `dh_ansible`
и создаются/обновляются по запросу через официальный localhost HTTPS API.

Первая синхронизация добавляет:

- `10-Cloudflare · Туннель объекта (план)` — пока **только read-only план**,
  не создаёт Access, DNS и туннель.
- `20-Проверка запуска Ansible` — smoke.
- `30-Аудит локального runner` — read-only.
- `40-Установить Remote Desktop Commander` — только по запросу инженера.
- `50-Проверка доступов PVE` — read-only.

Синхронизатор не удаляет неизвестные задания, не запускает новые шаблоны и
не меняет ID/историю существующих управляемых шаблонов. Работает через
ограниченный root-owned `systemd.path` обработчик. Отзывной API-токен Semaphore
хранится только у root (`0600`), поэтому смена UI-пароля не ломает синхронизацию.
При ошибке каталога задача `00` остаётся доступной. На восстановленном старом
CT1231 по-прежнему находятся прежние шаблоны; перенос требует отдельного аудита
и миграции, без уничтожения существующего Golden Backup.

**Историческая приёмка 20.10.2026:** старый Bootstrap использовал задачи
`10/20/30/40` и прошёл Fresh E2E. Новый одношаблонный Bootstrap прошёл живой CI-тест Semaphore 2.19 API, но ещё не принят в Fresh E2E.

При запуске нового шаблона `40-Установить Remote Desktop Commander` показывает
ссылку для подтверждения устройства и одноразовый код **в журнале Semaphore**.
Root-owned обработчик принимает только запросы `INSTALL:<uuid>` и `PAIR:<uuid>`,
читает только текущий запуск службы (systemd InvocationID), ограничивает возраст
кода 13 минутами и публикует исключительно очищенную строку статуса
в `/run/dh-remote-commander-pairing.status` (root:semaphore, `0640`).
Полный systemd journal, OAuth-сессия и секреты не предоставляются Semaphore.
Если служба уже работает, `PAIR` показывает текущий код без повторной установки.
Отдельно проверяется статус устройства Online: статус `active` в systemd его не доказывает.

Загрузка Chrome может инициироваться самим Desktop Commander для функции `write_pdf`.
Она не является частью процедуры авторизации Remote MCP.

У Semaphore нет общих root-прав. Для Remote Commander на runner заранее подготавливается root-owned фиксированный `systemd.path` / `oneshot` механизм. При запуске шаблон размещает только локальный запрос `INSTALL`; root выполняет исключительно закреплённый playbook установки Remote Commander. Все произвольные PVE/TrueNAS операции и доступ к Fleet Vault недоступны через этот механизм. Шаблон `50-Проверка доступов PVE` использует отдельный фиксированный read-only обработчик и не предоставляет Semaphore секреты.

### Fresh Bootstrap · transient pct exec SIGHUP

В E2E 20.10.2026 Bootstrap смог создать Debian 13, установить Ansible и HTTPS Semaphore,
но после `SEMAPHORE_TEMPLATES=PASS` один из вызовов `pct exec ... install -d` вернул `129`.
Exit 129 совместим с SIGHUP; причина сигнала по журналу не определена.
Только создание root-owned bootstrap-каталогов (идемпотентное `install -d`) теперь имеет
**не более 3 попыток** с диагностикой `GUEST_DIRECTORY_ATTACH_INTERRUPTED`.
Другие операции PVE, Semaphore, GitHub и Vault не повторяются автоматически.
Повторный полностью успешный Fresh E2E необходим перед созданием Golden backup.

### Debian 13 · штатная обработка Postfix в LXC

Для нового `CT1231` Bootstrap требует работающих `dbus.service`, `dbus.socket` и здорового systemd.
Если единственный упавший systemd-unit — `postfix.service`, который не нужен runner и не может
настроить mount namespace внутри непривилегированного LXC, Bootstrap штатно выполняет
`systemctl mask --now postfix.service` и `systemctl reset-failed postfix.service`.
Любые другие сбои systemd по-прежнему блокируют установку.

При остановке сразу после первого запуска Debian разрешён `--resume-fresh`.
Этот режим повторно не создаёт CT и **восстанавливает** ранее выданные PVE
API-токены и SSH-ключ из зашифрованного Fleet Vault — без перевыпуска токенов.

## Приёмка

20 октября 2026: **Fresh Bootstrap + recovery E2E `serzhan-home` = ACCEPTED**.
Действующий CT1231 создан заново из официального Debian 13. Во время первого запуска выявлена
ошибка необязательного Postfix в непривилегированном LXC; причина и обработка зафиксированы.
После возобновления подготовлены HTTPS Semaphore, Ansible Core 2.21.4, локальный inventory,
mDNS и PVE Access. Первоначальные credentials восстановлены из зашифрованного Fleet Vault
**без перевыпуска токенов**. В Semaphore проверены задания:
`10-Проверка запуска Ansible` Task #1 SUCCESS;
`20-Аудит локального runner` Task #2 SUCCESS;
`40-Проверка доступов PVE` Task #3 SUCCESS (`pve_access: PASS`).
systemd healthy, Semaphore и оба root-owned watcher active, секретный SSH-ключ root:0600.
Тест read-only PVE audit подтвердил реальные API-токены (GET) и SSH/sudo.
Проверка восстановленной инфраструктуры принята. Сценарии **PVE write** и развёртывание
NetAlertX являются отдельными задачами и этой приёмкой не утверждены.

19 октября 2026: **Fresh Bootstrap E2E домашнего pilot runner принят** — официальный Debian 13 с нуля, HTTPS Semaphore 2.19.16, Ansible Core 2.21.4, два UI smoke-аудита SUCCESS и read-only повторный seeder PASS. Это **не** разрешение на инфраструктурные write-операции или массовое развёртывание.

Новый flow подтверждения slug и on-demand Remote Commander проверяется отдельно CI и контролируемым `--resume-semaphore` на действующем тестовом CT; повторный destructive E2E для их применения не нужен. Production объекты не менять без отдельной приёмки.

## Имя локального runner

Hostname LXC: `ansible-<site_id>` (например, `ansible-serzhan-home`). Site ID (`serzhan-home`) остаётся ключом Fleet, Semaphore inventory и Vault; hostname всегда строится по этому полному ID. CTID/IP по прежнему стандарту 1231 / .231. Bootstrap сверяет Site ID и owner_slug из PRIVATE site metadata до создания гостя. Переход на новое имя применяется только при следующем Fresh E2E, старые работающие CT не переименовываются автоматически.


## Стандарт сетевой идентификации Linux-гостей

Для новых Linux LXC/VM, развёртываемых DigitalHouses, системный hostname должен
соответствовать декларативному имени объекта (у runner — `ansible-<site_id>`).
Помимо правильных `/etc/hostname` и `hostname`, гость публикует своё имя
в локальной сети через стандартный **Avahi/mDNS** — `<hostname>.local`.
Debian-runner устанавливает штатные `avahi-daemon` и `avahi-utils`,
включает `avahi-daemon.service` и проверяет, что mDNS разрешает
`ansible-<site_id>.local` в объявленный IPv4-адрес. Read-only runner audit
повторно проверяет активность службы и соответствие IP. Не используются
скрипты NetAlertX, искусственные имена, ручные `/etc/hosts` или
дублирование site-identity в исходном коде.

**Границы гарантии:** mDNS работает в пределах multicast-домена и зависит
от сетевой инфраструктуры (UDP 5353, 224.0.0.251). Этот стандарт **не создаёт**
DNS PTR в роутере и не гарантирует автоматического обновления уже заполненного
поля имени в NetAlertX. На принимающей стороне должен работать AVAHISCAN;
при старом имени следует проверить правила обновления `devName`
(`AVAHISCAN_SET_ALWAYS` — только по осознанной необходимости, с учётом
дополнительных запросов). Стандарт распространяется на новые Linux-гости;
изменение действующих production-гостей — отдельная проверяемая процедура.
