# Semaphore 2.20 Beta — чистый Bootstrap, 02.11.2026

## Живая приёмка Bootstrap по журналу владельца

**PASS — только свежая установка.** Представлен вывод запуска стандартного `bootstrap/install.sh` на PVE 01.11.2026 (в журнале файл `tiny-20261101T204020Z...`). Подтверждено:

- Проверенный pinned checkout `c206e4233503f2f9b0d18cf03c21b2ff08645819`.
- `CT_CREATE=PASS`; `CODE_GIT_SHA=PASS`; `ANSIBLE_RUNTIME=PASS`; `SEMAPHORE_PACKAGE=PASS`.
- `NETWORK_IDENTITY_MDNS=PASS`; `SEMAPHORE_DB_MIGRATION=PASS` (инициализация **новой** БД); `SEMAPHORE_SYSTEMD=PASS`; `SEMAPHORE_HTTPS=PASS`.
- `TASK_00_ONLY=PASS`; `BOOTSTRAP_FRESH_DONE=PASS`; `RESULT_RC=0`. Автоматического запуска инфраструктурных задач не было.
- Locale warnings в `apt` этапа 2 не фатальны; этап 3 подтвердил `GUEST_LOCALE=PASS C.UTF-8`.

**Не подтверждено:** ручной запуск №00, создание остального каталога, live E2E №05/06/07, работа по PVE API, реальная аутентификация пользователя через браузер. Remote Desktop Commander от удалённого гостя Offline, поэтому независимого внешнего наблюдения не было.

**Важный блокер перед №00:** `bootstrap/semaphore-project.py` в pinned checkout читает `https://api.github.com/repos/DigitalHouses/dh_ansible/commits/main` и каталог из `main`, **не из ветки PR #101**. На момент проверки в `main` — старые playbooks №05 GitHub Device Flow и №06 Fleet Vault; новая №05 из PR #101 не будет зарегистрирована обычной синхронизацией. Не путать успешную установку Beta с принятием нового credentials workflow. Прежде чем использовать №05/06, завершить и опубликовать согласованный каталог/реализацию; без ручных hotfix в LXC.


## Пилот новой №05/06 в draft PR #101

**Код реализован, live проверка НЕТ.** Каталог PR содержит `05-Ввести credentials объекта` и `06-Проверить credentials объекта`. №05 использует только native Ansible `uri` и `slurp` для работы с Semaphore API; собственный `scripts/semaphore-obtain-credentials.py` и связанный с ним старый E2E harness удалены. Пять необязательных полей формы сохраняют `target=env` для совместимости с уже установленным root-owned seeder №00. Секреты сохраняются как `var` в существующей Variable Group `Empty`, которую Seeder привязал ко всем задачам. №06 получает из неё PVE password через штатный механизм Semaphore 2.20 Ansible Vault transport.

№06 проверяет root@pam через HTTPS ticket, затем authenticated read-only PVE `/nodes`. До отправки пароля требуется host-root commissioned TLS fingerprint и публичный `pve-root-ca.pem`; generic `commission-pve-runner.py` в PR расширен копированием CA. Ни PVE API токены, ни отдельные Python-клиенты, ни новые хранилища секретов не введены.

**Статические проверки исходников:** 15/15 контрактных инвариантов проверены чтением GitHub файлов (точные названия, число полей, сохранение пустых, тип `var`, наличие HTTPS/TLS, ticket+nodes, отсутствие custom helper, CI syntax-check paths). Это **не** означает, что Ansible syntax, новая форма UI, Semaphore API PUT с type=var и реальная авторизация PVE прошли E2E: новая ветка не исполнялась на runner.

**CI:** GitHub Actions завершается на `Block unsafe public source tree before installing dependencies`, до pytest и ansible syntax. Не отключать эту проверку для публикации; отдельно провести source review и исправить public-boundary блокеры. PR остаётся Draft, `main` остаётся прежним, №00 в чистом runner до публикации нового каталога не запускать.

**Следующая приёмка:** после устранения CI-блокера и ревью — проверка native 05/06 на отдельном изолированном Semaphore 2.20.16-beta9 с `type=var` + live UI Survey + CA/hostname trust + read-only root@pam API. Публикация в main только после выполнения release gates; никакого ручного hotfix в CT1231.

## Предыстория подготовки

## Решение

Владелец разрешил переход на Semaphore `2.20.16-beta9` **без миграций**. Удаление CT1231 и стандартная установка — **две независимые операции**. Bootstrap `bootstrap/install.sh` сам **никогда не удаляет** существующие CT/VM.

Цель: PVE `<actual-PVE-IP>`, site `serzhan-home`, CT1231, ожидаемый IP `<runner-IP>`. Остальные гости и диски вне области работ.

## Проверено до запуска

- Официальный upstream-пакет `semaphore_2.20.16-beta9_linux_amd64.deb`, SHA256 `7e26db3b09b8e9fe5d8bd5c3956f3640dfc748b290f0b0b282746ba2633bf03a`: загружен временно и успешно проверен.
- Извлечённый бинарный файл сообщает `2.20.16-beta9`; команды `migrate`, `users add`, `server` существуют.
- На отдельном временном Semaphore Beta с тестовой SQLite и TLS пройден API E2E: вход, создание и частичное обновление 5 фиктивных credentials, metadata Key Store / Variable Groups, отсутствие plaintext секретов в SQLite.
- Стандартный `bootstrap/install.sh`: четыре этапа, pinned SHA, `pct create`, отказ если CT/VM1231 уже существует, только задача №00, **без `pct stop`/`pct destroy`**.
- Проверка исходников и тестовых контрактов — **не равнозначна живому E2E установки LXC на PVE**.

## Инцидент и исправление

Временно добавленный `bootstrap/replace-site-runner.sh` ошибочно совмещал удаление и развёртывание. При запуске оператор получил `Unknown option: timeout` на `pct stop --timeout 60`, `RESULT_RC=255`. Строка `CT1231_IDENTITY=PASS` подтверждает, что в момент запуска скрипт видел CT1231; никакого подтверждения успешного удаления этот журнал не содержит.

**Исправлено:** replacement-скрипт удалён из ветки. Запускать его повторно нельзя. Документация и тесты должны ссылаться только на стандартный `bootstrap/install.sh`. Указание оператора «CT1231 удалён вручную» принято; live-проверка состояния PVE агентом на момент записи недоступна (Remote Desktop Commander гостя недоступен после удаления, SSH к PVE без авторизации).

## Следующий шаг

В root Shell фактического PVE сначала **только read-only**:

```bash
hostname
ip -4 -o addr show vmbr0
pct config 1231
qm config 1231
```

Обе последние команды должны сообщить об отсутствии ресурса 1231. **Если CT1231 существует, остановиться, а не запускать новый скрипт для удаления.**

После проверки — запустить стандартную команду из [README](../../README.md#единая-точка-входа--tiny-bootstrap), ввести `serzhan-home` и подтвердить `y`. При `BOOTSTRAP_FRESH_DONE=PASS` проверить Semaphore version, HTTPS и единственную №00. №00 запускать вручную, №05–07 ещё не приняты.

**Live acceptance:** четырехэтапная установка PASS по операторскому журналу; №00 после установки и новые credentials workflows PENDING.


## Unit-тест №05/06: первый запуск, корректировка ожидания

Проверка владельца на CT1231 (Ansible Core 2.21.4 / Python 3.13.5), 02.11.2026: 7 тестов, **6 PASS, 1 FAIL**. Ошибка только в `test_partial_keep_and_safe_write_contract`: тест требовал Python-текст `if item in dh_supplied`, тогда как playbook корректно применяет Ansible `when: item in dh_supplied`. Исправление в ветке PR #101, commit `1d216c4bbd645584236211c149f10b999a86b36b`: условие проверяется структурно через `yaml.safe_load` и поиск шага `Prepare only submitted secret upserts`. Повторный живой unit test **PASS**: `Ran 7 tests in 0.025s`, `OK`, `ANSIBLE_05_06_UNIT_TESTS=PASS` (журнал владельца `1(20261101-215359).txt`). Все 7 контрактных unit-тестов прошли на CT1231. Это тесты структуры/контрактов: они **не проверяют** реальное сохранение секретов в Semaphore API. Ни один секрет и production Semaphore API не изменялись.

## Изолированная проверка синтаксиса на CT1231 — PASS

Владелец выполнил штатный `ansible-playbook --syntax-check` для обоих новых playbooks после исправления локали PVE и runner. Лог подтверждает:

```text
playbook: /tmp/tmp.oJNX3HHN0o/repo/playbooks/obtain-site-credentials.yml
playbook: /tmp/tmp.oJNX3HHN0o/repo/playbooks/check-site-credentials.yml
ANSIBLE_05_06_SYNTAX=PASS
```

Исполнялся Ansible Core 2.21.4 на уже установленном CT1231. Применения credentials/API writes не было. Модульные тесты, выполнение Survey и HTTPS Proxmox E2E **ещё не приняты**. Следующий шаг — выполнить unit-тесты из **того же PR**, затем изолированный API E2E на dummy secrets и уже после этого отдельный live test UI.

## Изолированный сценарий повторной проверки (справочно)

При отсутствии Remote Desktop Commander новая ветка может быть проверена на уже работающем CT1231 из **PVE root Shell**, не меняя git checkout Semaphore, базу, credentials или systemd. Временный checkout удаляется по завершении. Это тест **не** применяет №05/06 и не создаёт Secret Survey.

```bash
pct exec 1231 -- bash -c '
  set -euo pipefail
  d=$(mktemp -d /tmp/dh-native-credentials.XXXXXXXX)
  trap "rm -rf \"$d\"" EXIT
  git clone -q --depth 1 --branch feat/native-semaphore-credentials-05 \
    https://github.com/DigitalHouses/dh_ansible.git "$d/repo"
  /opt/ansible/venv/bin/ansible-playbook --syntax-check \
    -i "$d/repo/tests/fixtures/syntax_inventory.yml" \
    "$d/repo/playbooks/obtain-site-credentials.yml"
  /opt/ansible/venv/bin/ansible-playbook --syntax-check \
    -i "$d/repo/tests/fixtures/syntax_inventory.yml" \
    "$d/repo/playbooks/check-site-credentials.yml"
  /opt/ansible/venv/bin/python -m unittest discover -s "$d/repo/tests" \
    -p test_semaphore_native_obtain_pilot.py -v
  echo NATIVE_CREDENTIAL_SYNTAX_TEST=PASS
'
```

**Ожидаемый результат — не факт приёмки**, пока не получен настоящий вывод команды. На время теста секреты владельца не требуются. Пароль PVE в shell / git передавать нельзя.


## Изолированный E2E native Semaphore Variable Group (подготовлен, PENDING)

В PR добавлен `tests/e2e_semaphore_native_2_20.py`. Тест создаёт **только временный** Semaphore 2.20 Beta на случайном loopback-порту с отдельной SQLite, сертификатом и тестовой admin identity. Он запускается от непривилегированного пользователя `semaphore`, не читает действующую БД, ключи, API-токен и не использует production порт 3000. Значения строго фиктивные.

Проверки: `type=var` create двух секретов, сохранение несекретного MQTT username, частичное изменение только PVE root secret, сохранение другого secret/ID, отсутствие plaintext в API-ответах и во временной SQLite. Это **API/storage E2E**, а не реальный запуск Ansible №05/№06 или ввод через Semaphore UI.

Команда для PVE root Shell:

```bash
pct exec 1231 -- runuser -u semaphore -- bash -c '
set -euo pipefail
cd /tmp
d=$(mktemp -d)
trap "rm -rf \"$d\"" EXIT
git clone -q --depth 1 --branch feat/native-semaphore-credentials-05 \
  https://github.com/DigitalHouses/dh_ansible.git "$d/repo"
/opt/ansible/venv/bin/python "$d/repo/tests/e2e_semaphore_native_2_20.py"
'
```

**Первый живой запуск E2E — FAILED до операций с секретами.** Semaphore поднялся и ответил на `GET /api/ping`, но тестовый Python API-клиент ошибочно разбирал `pong` (plain text) как JSON. Лог владельца: `json.decoder.JSONDecodeError: Expecting value: line 1 column 1 (char 0)`. В upstream Semaphore 2.20 Beta ping endpoint действительно использует `plainTextMiddleware` и возвращает `pong`. Исправлено в `tests/e2e_semaphore_native_2_20.py` commit `c8582211b320576cf035199828188eb2234d3086`; теперь ping декодируется как текст и строго проверяется на `pong`. Повторный E2E **PASS** по выводу владельца: `NATIVE_VAR_CREATE=PASS`, `NATIVE_VAR_PARTIAL_KEEP=PASS`, `NATIVE_VAR_SQLITE_ENCRYPTION=PASS`, `SEMAPHORE_BETA_ISOLATED_NATIVE_E2E=PASS`. Запуск на CT1231 с отдельной SQLite, dummy credentials и временным HTTPS подтвердил нативное хранилище. Это не подтверждает запуск №05/06 через UI; следующий шаг — практическое включение задач после публикации каталога.

**Ожидаемый финальный маркер:** `SEMAPHORE_BETA_ISOLATED_NATIVE_E2E=PASS`. Результат изолированного API E2E подтверждён. Не запускать дополнительные изолированные тесты без конкретного нового блокера. На production Semaphore после этого шага ничего не меняется.
