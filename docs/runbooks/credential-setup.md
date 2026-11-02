> **LIVE PASS · 02.11.2026:** чистый Bootstrap создал PVE public trust, затем Semaphore №00 и №05 прошли; №06 через pinned TLS проверила `root@pam`, впервые авторизовала публичный SSH-ключ Ansible на PVE и доказала noninteractive SSH root + `qm list`. `pve=READY`, `pve_ssh=READY`, `overall=READY`. Не требуется №08 или ручной PVE host-root commissioning. Повтор №06 (запуск #4) успешно прошёл: создание ключа и password SSH enrollment пропущены, API/SSH `READY`; `changed=1` обусловлен только внутренним `add_host`, не изменениями на PVE. [Фактический отчёт](../acceptance/semaphore-beta-fresh-2026-11-02.md). Нижние PENDING/legacy инструкции — исторические, не применять к действующему CT.

## Целевой поток №06 — API + SSH commissioning (PR #112)

В одном задании **№06 «Проверить credentials объекта»** после №05 выполняются две проверки: `root@pam` API через доверенную PVE CA/hostname/SHA256 pin и SSH-доступ Ansible под root по site-scoped ключу. При первом запуске задача создаёт ключ и авторизует только его публичную часть через password SSH на host-key-pinned PVE, получив единственный сохранённый пароль из штатной Semaphore Variable Group. Повтор №06 не меняет PVE при уже работающем ключе. `overall=READY` означает готовность и API, и SSH. **№08 не существует и не будет создаваться.** Для этого Fresh Bootstrap должен заранее передать только **публичные** PVE trust materials с действительного PVE-хоста. [Описание реализации](https://github.com/DigitalHouses/dh_ansible/issues/111). До нового Fresh E2E работа ещё **не принята**.

> **Решение владельца после повторного Fresh Bootstrap, 02.11.2026:** commissioning PVE root SSH/TLS разрешён **только через Semaphore + Ansible**, без самостоятельной команды `commission-pve-runner.py` на PVE. На **текущем** заново установленном CT1231 №00 и №05 выполнились успешно, но №06 не может подтвердить PVE до появления доверенного CA/SSH identity; прежнее `READY` относится к удалённому предыдущему CT1231. Детали реализации — [issue #111](https://github.com/DigitalHouses/dh_ansible/issues/111). Пока нет нативной задачи и trusted seed, дальнейшие проверки PVE BLOCKED. Не предлагать ручной Shell PVE как штатное решение.

# Semaphore 2.20 Beta — получение и проверка credentials объекта (№05/06)

## Live acceptance · 02.11.2026 · serzhan-home

**READY (подтверждено живыми журналами):** №00 создала 12 задач и сохранила себя; №05 записала `DH_PVE_ROOT_PASSWORD` в Semaphore Variable Group без ошибок; PVE commissioning завершился `PVE_NATIVE_SSH=PASS`, `PVE_HARDWARE_AUDIT=PASS`, `PVE_COMMISSIONING=PASS`; №06 (run #6, source `50d5b3b`) прошла TLS/сертификат, аутентификацию `root@pam` и получение ожидаемого узла `pve`: `pve.status=READY`, `overall=READY`, `failed=0`. Объект `serzhan-home`, PVE `<actual-PVE-IP>`, runner `<runner-IP>`. Пароль повторно не требуется. Исправлены ошибки Semaphore inventory [#103](https://github.com/DigitalHouses/dh_ansible/pull/103), Jinja [#105](https://github.com/DigitalHouses/dh_ansible/pull/105), старого CA PVE8 [#107](https://github.com/DigitalHouses/dh_ansible/pull/107). Точный [отчёт приёмки](../acceptance/semaphore-beta-fresh-2026-11-02.md). **Не пройдены:** downstream задачи 10–90, GitHub №07, полный legacy CI.

## Назначение и статус

**Исторический промежуточный этап 02.11.2026:** №00 реально выполнилась (создано 12 шаблонов, failed=0), №05 успешно сохранила только PVE root password в native Semaphore Variable Group. Host-root PVE commissioning прошёл SSH/TLS/hardware PASS. Первое исполнение №06 обнаружило несовместимость Python 3.13 `VERIFY_X509_STRICT` с legacy Proxmox 8.4 CA без `keyUsage`; исправление №06 в [PR #107](https://github.com/DigitalHouses/dh_ansible/pull/107) прошло изолированные TLS-тесты, но реальный PVE login через обновлённую №06 ожидает проверки.** Не путать принятую чистую установку CT1231 / Semaphore 2.20.16-beta9 с проверкой нового credential workflow. Для новых объектов источником истины служит [ADR-0021](../decisions/0021-semaphore-native-credentials.md), а не исторический site Vault.

**Основной принцип:** Bootstrap создаёт только №00, которая читает ветку **main**. Новая №05/06 уже опубликована: запускайте №00 вручную в Semaphore, чтобы зарегистрировать обе задачи. Никаких ручных подмен файлов внутри CT1231 и переустановки runner.

## Что делает новая №05

Штатная форма Semaphore из пяти необязательных полей: GitHub provisioning token, MQTT login/password, PVE root password, Cloudflare API token. В начальном сценарии чистого PVE оператор вводит **только пароль PVE root**; имя пользователя фиксировано `root`, а HTTPS API identity — `root@pam`. Токены PVE не требуются.

- Каждое пустое поле = **KEEP**; повтор №05 изменяет только заполненные значения.
- №05 не подключается к PVE, не создаёт репозиторий и не проверяет корректность введённого пароля.
- Сохранение происходит штатными `ansible.builtin.uri` запросами к **локальному** HTTPS API Semaphore, с существующим site-local admin token и `no_log`, без собственного Python-клиента или новых secret stores.
- Используется существующая native Variable Group `Empty`: именно она уже прикреплена к шаблонам принятого четырёхэтапного Bootstrap. Имена групп не переименовываются «по пути»; родной root-owned seeder №00 закреплён в ранней ревизии. Секреты в ней хранятся как **тип `var`**, а MQTT login как обычная не-секретная переменная environment.
- При выполнении №06 Semaphore 2.20 передаёт Variable Group secrets типа `var` штатным зашифрованным Vault-файлом в Ansible.
- Существующий seeder №00 принимает Survey 05 только с `target=env`. Это вынужденная совместимость уже установленного бинарно закреплённого seeder, **не использование нового `target=var` для формы №05**. Нет секретов в shell argv, но значение формы временно присутствует в окружении Ansible controller; доступ к нему определяется доверенной границей site runner. Новый формат Survey `target=var` потребует отдельно принятого обновления seeder, а не ручного hotfix.

## Что делает новая №06

№06 не имеет Survey и не просит пароль повторно. Получает `DH_PVE_ROOT_PASSWORD` из Semaphore Variable Group Secrets и проверяет только обязательный для первой стадии PVE root@pam:

1. Если пароль не сохранён — `MISSING`.
2. Если нет заверенной с PVE идентичности / root CA — `PVE_TRUST_NOT_COMMISSIONED`. **Пароль не передаётся по сети.**
3. Сверяет TLS SHA256 fingerprint текущего сервера с зафиксированным PVE root commissioning. При отсутствии связи — `UNREACHABLE`; при изменении сертификата — `PVE_TLS_IDENTITY_MISMATCH`.
4. С помощью штатного Ansible `command` запускает небольшой TLS-клиент из того же публичного Git checkout `scripts/pve-https-credential-check.py`: доверенная PVE CA, имя узла и сохранённый SHA256-отпечаток сертификата проверяются **до передачи пароля**. Только для старой CA PVE 8 снимается флаг `VERIFY_X509_STRICT`; `CERT_REQUIRED`, `check_hostname` и SHA256-pin остаются включены. Пароль передаётся процессу по `stdin` (`no_log`), без argv или файлов, после чего выполняется `POST /api2/json/access/ticket`.
5. После HTTP 200 и ticket по **новому проверенному HTTPS-соединению** с повторной проверкой SHA256 выполняет `GET /api2/json/nodes`. Только HTTP 200 и наличие ожидаемого узла означает `READY`; `401/403` на первом запросе — `AUTH_FAILED`, на втором — `INSUFFICIENT_PRIVILEGES`. Ошибка CA/имени — `PVE_TLS_CERT_UNTRUSTED`, другая идентичность — `PVE_TLS_IDENTITY_MISMATCH`.
6. GitHub, MQTT и Cloudflare пока отображаются как `NOT_APPLICABLE` до commissioning соответствующих компонентов. Это **первая стадия** глобального readiness gate, а не завершённая проверка всех будущих сервисов.

Ticket, пароль, API token и request body не выводятся в Task output. Параметры с паролем не передаются в CLI argv и не сохраняются в отдельный файл. №06 ничего не развёртывает и не меняет на PVE.

## Исправление inventory в Semaphore 2.20 (№00 завершилась с skipped: no hosts matched)

**Причина подтверждена:** Semaphore 2.20.16-beta9 интерпретирует `type=file` inventory без `repository_id` относительно Git checkout, даже если в поле указан абсолютный путь. Поэтому Ansible ищет `.../repository_.../var/lib/ansible/site-data/<site>/hosts.yml`, не находит группу `site_runner` и **ложно завершается успешным skipping**. Это не запуск синхронизации.

Исправление [PR #103](https://github.com/DigitalHouses/dh_ansible/pull/103): стандартный Seeder создаёт *локальный Semaphore repository* `Site inventory (local)`, привязанный к root-owned `/var/lib/ansible/site-data/<site>`, и в inventory `Runner (local)` хранит только `hosts.yml` + `repository_id`. При обновлении сохраняется прежний inventory ID, привязки/история задач и существующая Variable Group с секретами. Не создаются публичные копии приватных inventory. №00 также имеет fail-closed preflight на случай отсутствия inventory.

Для существующего CT1231 уже установленный root-owned Seeder закреплён старой Git-ревизией. После merge выполните [версионированный upgrade](../../bootstrap/upgrade-semaphore-site-inventory.sh) из checkout **точного Git SHA** — внутри CT1231, PVE root shell использует `pct exec`. Он проверяет site и права, обновляет inventory через native Semaphore API, устанавливает обновлённый root-owned Seeder, проверяет идемпотентность, не требует ввода паролей и не меняет LXC/VM/disks. Успех: `SEMAPHORE_SITE_INVENTORY_UPGRADE=PASS`. Затем снова запустите №00; в журнале должен быть `result: PASS` с созданными/обновлёнными задачами, а не `skipping`.

## Предварительная доверительная привязка к PVE

Это **не дополнительное поле/секрет от владельца**. На чистом runner standard Bootstrap оставляет наблюдение `pve-runtime.yml` с `source: bootstrap_vmbr0`; этого недостаточно для передачи root-пароля по TLS.

Уже принятый **общий** PVE native commissioning `bootstrap/commission-pve-runner.py`, запускаемый из root Shell фактического PVE по проверенной pinned ревизии, устанавливает доверие к SSH/TLS на один объект и теперь копирует публичный `/etc/pve/pve-root-ca.pem` в `/var/lib/ansible/site-data/<site>/pve-root-ca.pem` (без приватных ключей). Подробности: [PVE native commissioning](pve-native-commissioning.md). В случае TLS/hostname mismatch проверки должны завершаться `BLOCKED`, а не отключать `validate_certs`.

**Текущий порядок после merge:** в Semaphore выполнить №00 → №05 (ввести только PVE root password) → выполнить generic PVE host-root commissioning с `PVE_COMMISSIONING=PASS`, если ещё не пройден → №06. При неверном пароле повтор №05 с одним полем, затем №06. Переустановка CT1231 для исправления пароля **не требуется**.

## Граница этой реализации

- Новый путь №05 и №06 опубликован в `main`; профильный CI 75/75 PASS и изолированный native Semaphore API/storage E2E PASS, но live UI и PVE TLS/root@pam E2E ещё **PENDING**.
- №07 GitHub private site repository и общий `pre_tasks` gate для всех действующих задач — отдельные утверждённые work items и пока **не реализованы**.
- Принятая 31.10.2026 историческая Fleet Vault авторизация относится к **старому** набору задач и не распространяется на новый сценарий.
- Сохранять доступ к базе Semaphore и отдельно её `access_key_encryption` для будущих бэкапов. Ни один секрет не должен существовать только как успешная строка Task 05.
