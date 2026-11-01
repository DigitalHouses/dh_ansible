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
