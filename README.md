# DigitalHouses · Ansible

Один универсальный набор кода для объектов DigitalHouses. **Дом отличается данными, а не кодом.**

| Репозиторий | Доступ | Назначение |
| --- | --- | --- |
| `DigitalHouses/dh_ansible` | PUBLIC | Ansible, Bootstrap, Semaphore, installer, CI и тесты |
| `DigitalHouses/dh_ansible_fleet_detail` | **PRIVATE** | Декларативные inventory объектов и зашифрованные Vault-файлы |

Секреты, реальные inventory и токены не публикуются в **публичном** Git. После №05/06 по решению владельца private Fleet Git credential и проверенный master password хранятся **локально на runner**, в каталоге пользователя `semaphore` с минимальными правами. Их не выводят в журнал.

**Обязательный сетевой стандарт:** все новые сетевые VM/LXC, создаваемые Ansible, должны публиковать своё имя `<hostname>.local` через Bonjour/mDNS (`avahi-daemon` для Linux без штатного mDNS; на других ОС — встроенный механизм) и проходить проверку обнаружения с другого узла LAN. Применяется ко всему Fleet, не только к медиасерверу. Правила безопасности, исключений и приёмки: [guest-network-mdns.md](docs/standards/guest-network-mdns.md).

**PVE IP — всегда фактический:** Proxmox-хост не участвует в нумерации VM/LXC. Bootstrap считывает IPv4 `vmbr0` непосредственно на PVE и сохраняет его **только в локальном runtime inventory** Ansible runner, не как исключение в Git. Задачи PVE, подключённые к общему загрузчику site inventory, используют этот адрес, а не устаревший `pve.host` из Fleet; legacy-задачи ещё требуют проверки. [Единый стандарт PVE endpoint](docs/standards/pve-endpoint-runtime.md).

**Обязательное именование ZFS-хранилищ:** вновь создаваемые Ansible пулы данных имеют имя `<owner>-pool` (например, `serzhan-pool`, где `owner` — `site.owner_slug` из Fleet). Существующие пулы при `keep + import` не переименовываются. [Стандарт именования ZFS-пулов](docs/standards/storage-pool-naming.md).

## AI-агентам: как самостоятельно запускать и проверять задачи

**После первого запуска №00 через Semaphore** остальные задачи создаются из GitHub-каталога. Все изменения infrastructure — только через утверждённые Semaphore tasks/Ansible и их согласованные approval. Для операций AI-агентов обязательно читать [операционный runbook](docs/runbooks/ai-agent-execution.md) и [AGENTS.md](AGENTS.md). Общие административные права для установки ПО и управления сервисами задаются по каждому managed target согласно [ADR-0020](docs/decisions/0020-infrastructure-privilege-and-software-installation.md), а не отдельными root-workers для каждого номера задачи.

**Предыдущая ревизия Fresh E2E принята 31.10.2026** на Jonsbo: чистый CT1231, `RESULT_RC=0`, стартовая **только №00**, затем успешный ручной запуск №00 через Semaphore: **10 создано, 1 без изменений, failed=0**. Подробная доказательная запись: [Tiny Bootstrap — Fresh E2E ACCEPTED](docs/acceptance/tiny-bootstrap-2026-10-31.md). Приёмка не распространяется на задачи 10–90, private Fleet и PVE Access.

## Единая точка входа · Tiny Bootstrap

Стандартная установка с чистого PVE: **одна команда** под `root` на выбранном Proxmox VE.

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/DigitalHouses/dh_ansible/6fef78d3cbd347552267c1bfd14bb0db5a683771/bootstrap/install.sh)
```

Установщик запрашивает и подтверждает Site ID, затем выполняет ровно четыре этапа:

1. Получить официальный шаблон Debian 13, создать **только отсутствующий** непривилегированный LXC (`CT1231`, адрес последнего октета `.231/24` из `vmbr0`).
2. Клонировать публичный `DigitalHouses/dh_ansible` **внутри нового LXC** и проверить закреплённый Git SHA.
3. Запустить внутри гостя существующий установщик Ansible + Semaphore (с TLS, mDNS, systemd, закрытым файлом начального пароля).
4. Подготовить минимальный локальный site inventory, через HTTPS API Semaphore создать **только** задачу `00-Обновить список задач` и включить ограниченный каталоговый watcher. Права для установки ПО не выдаются как побочный эффект Bootstrap.

Bootstrap **не** устанавливает Ansible на PVE, не требует GitHub OAuth и Fleet master password, не трогает private Fleet credentials, не создаёт Media workers или PVE tokens.

**Общий доступ для установки ПО:** №05/06 дают доступ к Fleet Vault, но не включают административные права Linux. Установка программ на runner и иных managed nodes требует отдельного commissioning инфраструктурной учётной записи / `ansible_become`. Задача №40 теперь использует обычную Ansible роль и общий preflight, а не особый watcher; при отсутствии прав она возвращает `INFRASTRUCTURE_ACCESS_NOT_COMMISSIONED`. Подробнее: [ADR-0020](docs/decisions/0020-infrastructure-privilege-and-software-installation.md) и [runbook №40](docs/runbooks/remote-commander-commissioning.md). Bootstrap не запускает №00; после установки оператор отдельно запускает №00 для получения полного каталога.

**Fresh only:** существующие CT/VM с ID 1231 не принимаются и не удаляются автоматически. Режимов `--resume` нет. 31.10.2026 владелец дал **новое явное разрешение** удалить только подтверждённый тестовый CT1231 для следующего Fresh E2E. Это не общая политика удаления других LXC/VM. Любой будущий отдельный destructive тест — только по [runbook](docs/runbooks/ai-agent-execution.md) с защитой HAOS VM2253. Успешный код возврата, единственная №00 и отдельный успешный запуск №00 — обязательная приёмка.

Начальный адрес Semaphore: `https://<runner-ip>:3000/`. Логин: `admin`. Пароль хранится только внутри LXC: `pct exec 1231 -- cat /root/dh-semaphore-initial-admin-password`; не публиковать пароль в чат. Логи установщика на PVE: `/var/log/digitalhouses/ansible/tiny-*.log` (`root:root 0600`).

## Первое получение credentials — задачи №05 и №06

После `00-Обновить список задач` в UI появляются две дополнительные административные операции:

- **`05-Получить credentials GitHub`** — GitHub CLI показывает в журнале одноразовый код. Оператор подтверждает его на `https://github.com/login/device`; задача ждёт разрешения, проверяет доступ к `DigitalHouses/dh_ansible_fleet_detail` и клонирует private Fleet на runner. Пока GitHub-токен работает, повторный ввод кода не требуется.
- **`06-Ввести master password Fleet Vault`** — одно скрытое поле Semaphore типа **secret**. Пароль проверяется через `ansible-vault view`, сохраняется локально `0600` и остаётся доступным после перезагрузки. Не передавать его в чат или терминальный журнал.

**Хранение — минимально:** `/var/lib/semaphore/.dh-credentials/` (пользователь `semaphore`, каталог `0700`; GitHub CLI config, private Fleet checkout и `fleet-vault-password` с `0600`). Master password хранится локально в открытом виде **по решению оператора**, но не в публичном Git. Никаких новых служб или центрального Credentials Provider.

**Для Ansible:** role `fleet_credentials` читает Vault через сохранённый пароль и предоставляет `fleet_site_credentials` только для текущего `site.id`. Другие задачи должны явно включать эту роль, когда им нужны secrets; старые protected workers/PVE Access не появляются автоматически от успешной №05/06.

**Следующий тест:** по новому явному разрешению владельца удаляем **только идентифицированный тестовый CT1231** и запускаем эту же единую команду **с нуля**. Новая закреплённая ревизия сразу содержит поддержку поля `secret` и задачи №05/06 — без ручного обновления файлов, `--resume` или правки старого LXC. Живой E2E этой ревизии ещё не проведён; №05/06 будут проверены отдельно после №00.

## PVE Access · отдельная приёмка, не часть Bootstrap

Историческая приёмка PVE Access и восстановления Fleet Vault на **прежнем PVE** проходила в октябре 2026 года. Tiny Bootstrap на Jonsbo **не** переносит identities, private Fleet и защищённые workers; они требуют отдельных задач Semaphore, явного разрешения и собственных проверок. Старые результаты нельзя принимать как подтверждение нового PVE.

## Semaphore · локальные операции

**Обязательный стандарт проектирования и выбора механизма задач:** [Semaphore · создание задач, reusable-этапы и Workflows](docs/standards/semaphore-task-design.md). Перед добавлением нового шаблона сверяйте функциональный класс, источник site data/секретов, границу root-worker и E2E-приёмку одной кнопкой.


**Принятый режим (Fresh E2E + первый запуск №00 = PASS, 31.10.2026):** Установщик создаёт в Semaphore
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
При ошибке синхронизации задача №00 остаётся доступной. Уже существующие задачи нельзя стирать или переименовывать без отдельного проверенного плана.

**Текущая приёмка:** только четырёхэтапный Tiny Bootstrap и №00; 10 новых задач зарегистрированы, но не запускались. Исторические результаты старого Bootstrap не заменяют приемку инфраструктурных задач на Jonsbo.

Новая задача **40-Установить Remote Desktop Commander** вызывает общий `infrastructure_access` и существующую Ansible-роль `runner_remote_commander` с `become: true`. При действительном административном доступе она устанавливает pinned Node.js и агент, управляет systemd и выводит отфильтрованный код подключения. Отдельно проверяется статус устройства `Online`: статус `active` в systemd его не доказывает.

Загрузка Chrome может инициироваться самим Desktop Commander для функции `write_pdf`.
Она не является частью процедуры авторизации Remote MCP.

**Правила для всех задач установки ПО:** target, административная identity/credential source, `become`, разрешённые операции, approval и postconditions должны быть явно указаны. `sudo`/root-доступ — общий для конкретного managed node инфраструктурный prerequisite, а не исключение под приложение. См. [ADR-0020](docs/decisions/0020-infrastructure-privilege-and-software-installation.md) и [стандарт Semaphore](docs/standards/semaphore-task-design.md). Существующие PVE/Media workers считаются legacy-контрактами до отдельной миграции; новая модель не устанавливает их автоматически.

## Приёмка нового установщика

**31 октября 2026:** подтверждены Fresh Bootstrap и ручной запуск задачи №00 через Semaphore. Журнал Bootstrap: `CT_CREATE=PASS`, `CODE_GIT_SHA=PASS`, `SEMAPHORE_INSTALL=PASS`, `TASK_00_ONLY=PASS`, `BOOTSTRAP_FRESH_DONE=PASS`, `RESULT_RC=0`. Журнал №00: `created=10`, `unchanged=1`, `updated=0`, `failed=0`, `unreachable=0`, `infrastructure_tasks_executed=false`. Полный отчёт: [acceptance](docs/acceptance/tiny-bootstrap-2026-10-31.md).

**Ранее:** в октябре 2026 прежний Python Bootstrap проходил собственные E2E и диагностику `pct exec`/Postfix, включал восстановление Fleet/PVE identities и режимы `--resume*`. Эти механизмы **не относятся к принятому Tiny Bootstrap**, не должны возвращаться в его путь и остаются только историей Git. Отдельная проверка PVE/Fleet/Media/Remote Commander на новом runner ещё не проведена.

**CI:** общий workflow по-прежнему останавливается на проверке безопасности некоторых ранее существовавших публичных файлов, ещё до тестов. Этот вопрос решается отдельно, без обхода security gate; успешный live E2E не означает зелёный CI.

## Имя локального runner

Hostname LXC: `ansible-<site_id>` (например, `ansible-serzhan-home`). Site ID (`serzhan-home`) остаётся ключом Fleet, Semaphore inventory и Vault; hostname всегда строится по этому полному ID. CTID/IP по прежнему стандарту 1231 / .231. Tiny Bootstrap подтверждает введённый Site ID и строит hostname из него; **private Fleet до создания CT не загружается**. Переход на новое имя применяется только при следующем Fresh E2E, старые работающие CT не переименовываются автоматически.


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
