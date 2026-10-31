# Tiny Bootstrap + Semaphore №00 — Fresh E2E ACCEPTED

**Дата:** 2026-10-31  
**Объект:** Jonsbo pilot, `site=serzhan-home`, `CT1231` (`ansible-serzhan-home`)  
**Статус:** **ACCEPTED — только Tiny Bootstrap и первый запуск задачи №00**  
**Источник проверки:** два предоставленных оператором журнала фактического запуска на PVE и в Semaphore. Секреты, адреса LAN и содержимое private Fleet в этот публичный документ не переносились.

## Зафиксированные immutable revisions

- Запущенный публичный `bootstrap/install.sh`: `261645fb4ab49bad8c0b115a49e88db7a47029fe`.
- Закреплённый гостевой код `CODE_GIT_SHA`: `2dbcfcdc7e6e0bd34bb547b41a888aab92c755df`.
- Каталог, успешно импортированный **вручную запущенной** №00: `21e35c20cfac1ed1650aeef8d484cf197cee147e`.

## Фактический Fresh Bootstrap

Перед запуском оператор проверил идентичность тестового CT1231, удалил **только** его вместе с test rootfs и подтвердил отсутствие контейнера. Защищённые другие VM/LXC не были целью удаления. Далее из одной команды с чистого состояния:

| Этап | Наблюдаемый результат |
| --- | --- |
| Debian 13 / unprivileged CT | `CT_CREATE=PASS` |
| Git checkout внутри CT | `CODE_GIT_SHA=PASS` |
| Ansible runtime / mDNS / Semaphore HTTPS | `ANSIBLE_RUNTIME=PASS`, `NETWORK_IDENTITY_MDNS=PASS`, `SEMAPHORE_HTTPS=PASS` |
| Семейство служб Semaphore и начальный администратор | `SEMAPHORE_INSTALL=PASS` |
| Первоначальный каталог **только №00** | `TASK_00_ONLY=PASS`, `NO_INFRASTRUCTURE_TASKS_LAUNCHED=YES` |
| Итог установки | `BOOTSTRAP_FRESH_DONE=PASS`, `RESULT_RC=0` |

Bootstrap не выполнял GitHub OAuth, не читал private Fleet Vault, не регистрировал PVE credentials, не устанавливал Media workers и не запускал задачи 10–90.

## Фактический запуск задачи №00 через Semaphore

Оператор запустил **`00-Обновить список задач`** в UI Semaphore. Задача №1 клонировала GitHub-код и успешно выполнила `playbooks/request-semaphore-catalog-sync.yml`.

- `result=PASS`, `source_commit=21e35c20cfac1ed1650aeef8d484cf197cee147e`.
- `created=10`, `updated=0`, `unchanged=1` (исходная №00 сохранена).
- `infrastructure_tasks_executed=false`.
- Ansible recap: `ok=11`, `changed=1`, `failed=0`, `unreachable=0`.

## Граница приёмки и дальнейшая эксплуатация

**Принято:** создание чистого CT, публичный pinned checkout, Ansible, Semaphore HTTPS, единственная стартовая №00 и успешная **пользовательская** синхронизация ещё десяти задач из GitHub.

**Не утверждалось этой приёмкой:** успешность задач 10–90, доступ PVE/Fleet, восстановление секретов, Remote Commander, media worker, развёртывание приложений и какие-либо инфраструктурные write-операции. Всё это требует своих preflight/approval/acceptance.

**Fresh E2E test-only cleanup закрыт:** принятый CT1231 нельзя автоматически уничтожать для очередного теста. `bootstrap/install.sh` никогда не удаляет существующие CT/VM и остаётся fresh-only; повторный destructive test возможен только с новым адресным разрешением и проверками.

**Независимая оговорка CI:** на дату приёмки общий GitHub CI не зелёный: `bin/public-boundary-check.py` обнаруживает ранее существующие проблемные файлы в публичном дереве и останавливает workflow до unit/syntax-тестов. Этот security gate нельзя обходить или объявлять PASS из-за успешного live Fresh E2E. Требуется отдельная безопасная ревизия выявленных файлов и истории Git.

**Стандарт будущих установок:** [README](../../README.md) → одна pinned команда → единственная №00 → пользователь вручную запускает №00 в Semaphore → остальные шаблоны из GitHub. Не возвращать старый Python-orchestrator.
