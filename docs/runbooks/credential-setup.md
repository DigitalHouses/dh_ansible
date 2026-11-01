# Semaphore 2.20 Beta — получение и проверка credentials объекта (№05/06)

## Назначение и статус

**Новая реализация в PR #101; live E2E №05/06 ещё не выполнен.** Не путать принятую чистую установку CT1231 / Semaphore 2.20.16-beta9 с проверкой нового credential workflow. Для новых объектов источником истины служит [ADR-0021](../decisions/0021-semaphore-native-credentials.md), а не исторический site Vault.

**Основной принцип:** Bootstrap создаёт только №00. Она загружает каталог из ветки **main**. Если PR ещё не принят в main, №00 регистрирует **прежнюю** №05 GitHub/№06 Fleet Vault, а не новые задачи. Ручных подмен Python-кода и файлов на runner не делать.

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
4. Выполняет запрос `POST /api2/json/access/ticket` стандартным `ansible.builtin.uri` с проверкой сертификата (`validate_certs: true`), login `root@pam`, введённым паролем. `401/403` — `AUTH_FAILED`; HTTP 200 с ticket — `READY`; прочее — `PVE_TLS_OR_API_UNAVAILABLE`.
5. GitHub, MQTT и Cloudflare пока отображаются как `NOT_APPLICABLE` до commissioning соответствующих компонентов. Это **первая стадия** глобального readiness gate, а не завершённая проверка всех будущих сервисов.

Ticket, пароль, API token и request body не выводятся в Task output. №06 ничего не развёртывает и не меняет на PVE.

## Предварительная доверительная привязка к PVE

Это **не дополнительное поле/секрет от владельца**. На чистом runner standard Bootstrap оставляет наблюдение `pve-runtime.yml` с `source: bootstrap_vmbr0`; этого недостаточно для передачи root-пароля по TLS.

Уже принятый **общий** PVE native commissioning `bootstrap/commission-pve-runner.py`, запускаемый из root Shell фактического PVE по проверенной pinned ревизии, устанавливает доверие к SSH/TLS на один объект и теперь копирует публичный `/etc/pve/pve-root-ca.pem` в `/var/lib/ansible/site-data/<site>/pve-root-ca.pem` (без приватных ключей). Подробности: [PVE native commissioning](pve-native-commissioning.md). В случае TLS/hostname mismatch проверки должны завершаться `BLOCKED`, а не отключать `validate_certs`.

**Порядок приёмки после публикации каталога:** один раз выполнить generic PVE commissioning с проверкой `PVE_COMMISSIONING=PASS` → в Semaphore запустить №00 → №05 (PVE root password) → №06. При неверном пароле повтор №05 с одним полем, затем №06. Переустановка CT1231 для исправления пароля **не требуется**.

## Граница этой реализации

- Новый путь №05 и №06 — нативные Ansible playbooks, но live UI E2E ещё **PENDING**.
- №07 GitHub private site repository и общий `pre_tasks` gate для всех действующих задач — отдельные утверждённые work items и пока **не реализованы**.
- Принятая 31.10.2026 историческая Fleet Vault авторизация относится к **старому** набору задач и не распространяется на новый сценарий.
- Сохранять доступ к базе Semaphore и отдельно её `access_key_encryption` для будущих бэкапов. Ни один секрет не должен существовать только как успешная строка Task 05.
