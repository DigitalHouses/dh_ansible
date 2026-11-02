**Task 05 · MQTT endpoint:** после повторной №00 штатная форма №05 включает `MQTT · IP-адрес или имя брокера`, логин и пароль. Адрес — site-specific текстовое значение `DH_MQTT_BROKER_HOST` (без протокола и порта, порт по умолчанию 1883), не фиксирован в public Git. Пустое значение = KEEP. №00 расширяет прежнюю форму на месте с сохранением ID, истории и credentials, **не пересоздавая CT1231**. №06 пока проверяет PVE API+SSH; GitHub/MQTT/Cloudflare пока не относятся к её READY. 

**LIVE PASS, 02.11.2026:** на полностью пересозданном CT1231 Bootstrap → №00 → №05 → №06 успешно подключили PVE API **и root SSH Ansible**, `pve=READY`, `pve_ssh=READY`, `overall=READY` без host-root commissioning вручную и без №08. [Доказательная запись](docs/acceptance/semaphore-beta-fresh-2026-11-02.md). Второй запуск №06 успешно подтвердил идемпотентность: новый ключ и повторное password SSH enrollment пропущены; SSH по ключу и API `READY`, изменений PVE нет. Downstream задачи 10–90 пока не приняты. Исторические PENDING ниже сохранены как хронология.

**После первоначального Bootstrap доступ к PVE подключается только штатной задачей Semaphore №06.** Целевой единый поток: Bootstrap (публичная PVE CA/TLS/SSH trust из исходного PVE-host-root) → №00 → №05 (только PVE root password) → №06 (API root@pam, key enrollment через pinned SSH host, подтверждённый root SSH). **№08 не вводится; ручной PVE commissioning больше не нормативен.** Реализация [PR #112](https://github.com/DigitalHouses/dh_ansible/pull/112) и [issue #111](https://github.com/DigitalHouses/dh_ansible/issues/111), live Fresh E2E ещё ожидается. Текущий CT1231 нельзя применять к новому сценарию без доверенного начального trust seed. Не удалять его автоматически.

# DigitalHouses · Ansible

Один универсальный набор кода для объектов DigitalHouses. **Дом отличается данными, а не кодом.**

| Репозиторий | Доступ | Назначение |
| --- | --- | --- |
| `DigitalHouses/dh_ansible` | PUBLIC | Ansible, Bootstrap, Semaphore, installer, CI и тесты |
| `DigitalHouses/dh_ansible_fleet_detail` | **PRIVATE** | Декларативные inventory объектов и зашифрованные Vault-файлы |

Секреты, реальные inventory и токены не публикуются в **публичном** Git. После №05/06 по решению владельца private Fleet Git credential и проверенный master password хранятся **локально на runner**, в каталоге пользователя `semaphore` с минимальными правами. Их не выводят в журнал.

**Обязательный сетевой стандарт:** все новые сетевые VM/LXC, создаваемые Ansible, должны публиковать своё имя `<hostname>.local` через Bonjour/mDNS (`avahi-daemon` для Linux без штатного mDNS; на других ОС — встроенный механизм) и проходить проверку обнаружения с другого узла LAN. Применяется ко всему Fleet, не только к медиасерверу. Правила безопасности, исключений и приёмки: [guest-network-mdns.md](docs/standards/guest-network-mdns.md).

**Новый объект Jonsbo:** при переходе c `serzhan-home` на самостоятельный `serzhan-lab` использовать [Fresh runbook](docs/runbooks/serzhan-lab-fresh.md). Он заменяет только CT1231 после строгого preflight; VM2253 и диски не затрагиваются. После переустановки требуются новая авторизация №05/06 и привязка №40.

**PVE IP — всегда фактический:** Proxmox-хост не участвует в нумерации VM/LXC. Bootstrap считывает IPv4 `vmbr0` непосредственно на PVE и сохраняет его **только в локальном runtime inventory** Ansible runner, не как исключение в Git. Задачи PVE, подключённые к общему загрузчику site inventory, используют этот адрес, а не устаревший `pve.host` из Fleet; legacy-задачи ещё требуют проверки. [Единый стандарт PVE endpoint](docs/standards/pve-endpoint-runtime.md). Для постоянного site runner подключение к PVE выполняется **однократно с хоста PVE** через [универсальный commissioning](docs/runbooks/pve-native-commissioning.md), без task-specific workers.

**Обязательное именование ZFS-хранилищ:** вновь создаваемые Ansible пулы данных имеют имя `<owner>-pool` (например, `serzhan-pool`, где `owner` — `site.owner_slug` из Fleet). Существующие пулы при `keep + import` не переименовываются. [Стандарт именования ZFS-пулов](docs/standards/storage-pool-naming.md).

## AI-агентам: как самостоятельно запускать и проверять задачи

**Новые объекты и исправление паролей:** №05 позволяет повторно ввести только ошибочный credential, оставляя остальные сохранённые значения неизменными. После №05 (сохранение всех credentials) и успешной №06 (проверка) отдельная №07 создаёт приватный GitHub repo через штатный API; её повторное выполнение безопасно проверяет/adopt существующий репозиторий. Репозиторий объекта хранит конфигурацию и inventory, а секреты — только Semaphore. Это целевой контракт [ADR-0021](docs/decisions/0021-semaphore-native-credentials.md), пока **не внедрён**.

**LIVE ACCEPTED · 02.11.2026 — `serzhan-home`:** стандартный четырёхэтапный Bootstrap, №00 (12 задач создано, 1 сохранена), №05 (введён только root-пароль PVE), native host-root commissioning и №06 (`root@pam=READY`, `overall=READY`, `failed=0`) приняты по реальным журналам Semaphore. PVE `<actual-PVE-IP>`, runner CT1231 `<runner-IP>`. Для PVE 8 legacy CA без Key Usage в №06 принята scoped TLS compatibility [PR #107](https://github.com/DigitalHouses/dh_ansible/pull/107): CA/hostname/cert pin обязательны. Отчёт: [Semaphore Beta live acceptance](docs/acceptance/semaphore-beta-fresh-2026-11-02.md). **Это не приёмка №07, задач 10–90 и полного legacy CI** ([issue #102](https://github.com/DigitalHouses/dh_ansible/issues/102)). Существующий CT1231 не переустанавливать.

**Историческая публикация №05/06 (до live acceptance):** [ADR-0021 — Semaphore-native credentials](docs/decisions/0021-semaphore-native-credentials.md), [PR #101](https://github.com/DigitalHouses/dh_ansible/pull/101) объединён с `main` (`b883ff85`). №05/06 используют native Semaphore Variable Group Secrets и Ansible URI. Профильный CI — 75/75 PASS, syntax PASS, изолированный API E2E PASS; **живой UI/PVE E2E ещё не завершён**, №07 и глобальный preflight не реализованы. Общий CI остаётся красным (59 исторических ошибок других подсистем), проверки не отключали.

**Credentials новых домов:** целевой стандарт — [native Semaphore](docs/runbooks/credential-setup.md), без обязательного параллельного site Vault. [Исторический site Vault onboarding](docs/runbooks/site-credentials-onboarding.md) нужен только при сопровождении прежнего профиля, а не для чистой №05/06 на Beta.

**После первого запуска №00 через Semaphore** остальные задачи создаются из GitHub-каталога. Все изменения infrastructure — только через утверждённые Semaphore tasks/Ansible и их согласованные approval. Для операций AI-агентов обязательно читать [операционный runbook](docs/runbooks/ai-agent-execution.md) и [AGENTS.md](AGENTS.md). Общие административные права для установки ПО и управления сервисами задаются по каждому managed target согласно [ADR-0020](docs/decisions/0020-infrastructure-privilege-and-software-installation.md), а не отдельными root-workers для каждого номера задачи.

**Предыдущая ревизия Fresh E2E принята 31.10.2026** на Jonsbo: чистый CT1231, `RESULT_RC=0`, стартовая **только №00**, затем успешный ручной запуск №00 через Semaphore: **10 создано, 1 без изменений, failed=0**. Подробная доказательная запись: [Tiny Bootstrap — Fresh E2E ACCEPTED](docs/acceptance/tiny-bootstrap-2026-10-31.md). Приёмка не распространяется на задачи 10–90, private Fleet и PVE Access.

## Локаль PVE и Ansible runner

**Единое значение для headless Debian/Proxmox: `LANG=C.UTF-8`.** Не задавать `LC_ALL` глобально: он переопределяет все `LC_*`. Устанавливать его только для конкретной команды, если нужно подавить унаследованную с PVE несовместимую локаль. Новый Bootstrap нормализует окружение перед `pct`, гостевой installer записывает `/etc/default/locale`, а systemd Semaphore уже получает `LANG=C.UTF-8` и `LC_ALL=C.UTF-8` локально в unit.

**На уже установленном CT1231 код будущего Bootstrap сам собой не применяется.** Оператор один раз задаёт локаль PVE через `localectl set-locale LANG=C.UTF-8`, заново открывает Shell и проверяет `locale` / `locale -a`; для существующего runner аналогично правит `/etc/default/locale` под root, **не пересоздавая CT**. При ручном `pct exec` можно изолировать запуск: `pct exec 1231 -- env LANG=C.UTF-8 LC_ALL=C.UTF-8 /opt/ansible/venv/bin/ansible-playbook --version`. Не ставить локаль `en_US.UTF-8`, если её нет в `locale -a`.

## Единая точка входа · Tiny Bootstrap

**Версия на 02.11.2026:** установщик зафиксирован на commit `ab95ec0`: включает исправления №00 (путь локального Inventory), №05 (Jinja), №06 (совместимость TLS с CA Proxmox 8) и корректную обработку Debian 13 `/etc/locale.conf` при стандартной ссылке `/etc/default/locale`. Предыдущий Fresh E2E достиг `CT_CREATE=PASS` и `CODE_GIT_SHA=PASS`, затем остановился на `Unexpected /etc/default/locale file type` до установки Ansible/Semaphore; исправление проверено автоматическими тестами, **повторный Fresh E2E ещё ожидается**. Это версия для **новых чистых объектов**, не команда обновления работающего CT1231. На CT1231 №00 уже выполнилась успешно (12 создано, 1 без изменений), а №05 и №06 прошли живую проверку на текущем CT1231. Повторная установка CT1231 запрещена без нового явного разрешения.



Стандартная установка с чистого PVE: **одна команда** под `root` на выбранном Proxmox VE. Релиз этого пилота — **Semaphore 2.20.16-beta9**, закреплённый SHA256. Перед запуском CT/VM 1231 должен отсутствовать; автоматического удаления нет. Для этого пилота указать Site ID `serzhan-home`.

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/DigitalHouses/dh_ansible/eee069453df1aa3d3d97315fffd1cfdb0a6e2e20/bootstrap/install.sh)
```

Установщик запрашивает и подтверждает Site ID, затем выполняет ровно четыре этапа:

1. Получить официальный шаблон Debian 13, создать **только отсутствующий** непривилегированный LXC (`CT1231`, адрес последнего октета `.231/24` из `vmbr0`).
2. Клонировать публичный `DigitalHouses/dh_ansible` **внутри нового LXC** и проверить закреплённый Git SHA.
3. Запустить внутри гостя существующий установщик Ansible + Semaphore (с TLS, mDNS, systemd, закрытым файлом начального пароля).
4. Подготовить минимальный локальный site inventory, через HTTPS API Semaphore создать **только** задачу `00-Обновить список задач` и включить ограниченный каталоговый watcher. Права для установки ПО не выдаются как побочный эффект Bootstrap.

Bootstrap **не** устанавливает Ansible на PVE, не требует GitHub OAuth и Fleet master password, не трогает private Fleet credentials, не создаёт Media workers или PVE tokens.

**Общий доступ для установки ПО:** №05/06 теперь сохраняют и проверяют нативные credentials Semaphore, но не включают административные права Linux. Установка программ на runner и иных managed nodes требует отдельного commissioning инфраструктурной учётной записи / `ansible_become`. Задача №40 теперь использует обычную Ansible роль и общий preflight, а не особый watcher; при отсутствии прав она возвращает `INFRASTRUCTURE_ACCESS_NOT_COMMISSIONED`. Подробнее: [ADR-0020](docs/decisions/0020-infrastructure-privilege-and-software-installation.md) и [runbook №40](docs/runbooks/remote-commander-commissioning.md). Bootstrap не запускает №00; после установки оператор отдельно запускает №00 для получения полного каталога.

**Fresh only:** существующие CT/VM с ID 1231 не принимаются и не удаляются автоматически. Режимов `--resume` нет. 31.10.2026 владелец дал **новое явное разрешение** удалить только подтверждённый тестовый CT1231 для следующего Fresh E2E. Это не общая политика удаления других LXC/VM. Любой будущий отдельный destructive тест — только по [runbook](docs/runbooks/ai-agent-execution.md) с защитой HAOS VM2253. Успешный код возврата, единственная №00 и отдельный успешный запуск №00 — обязательная приёмка.

Начальный адрес Semaphore: `https://<runner-ip>:3000/`. Логин: `admin`. Пароль хранится только внутри LXC: `pct exec 1231 -- cat /root/dh-semaphore-initial-admin-password`; не публиковать пароль в чат. Логи установщика на PVE: `/var/log/digitalhouses/ansible/tiny-*.log` (`root:root 0600`).

## Первое получение credentials — задачи №05 и №06 (Semaphore Beta)

**Исправление №00 от 02.11.2026:** в старом Bootstrap Semaphore `type=file` inventory ошибочно разрешался относительно public Git checkout и №00 выводила `skipping: no hosts matched`. Выпущен [PR #103](https://github.com/DigitalHouses/dh_ansible/pull/103): будущий новый Bootstrap уже включает исправление, а для **установленного CT1231** используется строго version-pinned [in-place upgrade](bootstrap/upgrade-semaphore-site-inventory.sh) без переустановки; подробности в [runbook](docs/runbooks/credential-setup.md#исправление-inventory-в-semaphore-220-00-завершилась-с-skipped-no-hosts-matched). После его успешного применения снова запустить №00.

**После свежего Bootstrap** в Semaphore есть только №00. Она синхронизирует каталог **из main**; PR #101 уже объединён, поэтому №00 можно запускать для получения №05/06. Не проводить ручную замену root-owned seeder и не возвращаться к старому GitHub Device Flow/Fleet master password.

- **№05 · Ввести credentials объекта:** штатная Survey-форма с пятью необязательными полями. Для первого подключения чистого Proxmox нужен **только пароль PVE root**. Пустые поля оставляют сохранённые credentials без изменений. Ansible сохраняет новые значения через native Semaphore API в Variable Group Secrets, без собственного Python-клиента. Пароль не выводится в задачах.
- **№06 · Проверить credentials объекта:** не требует повторного пароля, берёт PVE secret из Semaphore, проверяет сертификат и идентичность PVE, аутентифицируется `root@pam` через HTTPS ticket и читает список PVE nodes. Без удостоверенного TLS доверия выводит `PVE_TRUST_NOT_COMMISSIONED`, **не передавая пароль**. GitHub/MQTT/Cloudflare пока `NOT_APPLICABLE` до своих этапов.

До №06 выполняется принятый общий [PVE native commissioning](docs/runbooks/pve-native-commissioning.md) с PVE root Shell (публичный CA, SSH/TLS trust, без дополнительных паролей). Подробный [операторский runbook №05/06](docs/runbooks/credential-setup.md). **Live E2E новой схемы и №07 пока не приняты.**


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

**CI (02.11.2026):** проверка публичных данных PASS. Профильная проверка №05/06: 75/75 PASS и обе Ansible syntax PASS. Общий pytest всё ещё содержит 59 ошибок исторических контрактов других подсистем; общий CI красный, проверки не отключены.

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
