# Fleet credentials №00 → №05 → №06 — Fresh E2E ACCEPTED

**Дата:** 2026-10-31  
**Объект:** Jonsbo pilot, `site=serzhan-home`, `CT1231`  
**Статус:** **ACCEPTED для первичной загрузки private Fleet и однократного ввода master password**. Это не приёмка downstream задач 10–90, PVE root workers, автоматического обновления Git checkout или повторного запуска после reboot.

## Evidence: fresh pinned Bootstrap

Оператор на PVE явно проверил идентичность **только** тестового CT1231, удалил его и выполнил один штатный fresh-only Bootstrap без ручных hotfix. Лог содержит:

- `TARGET_VERIFIED=PASS`, `LXC_1231_REMOVED=PASS`.
- `CODE_GIT_SHA=PASS b6c727951afb68161c0b073b642780efecf0b165`.
- `CT_CREATE=PASS`, `ANSIBLE_RUNTIME=PASS`, `NETWORK_IDENTITY_MDNS=PASS`, `SEMAPHORE_HTTPS=PASS`.
- `TASK_00_ONLY=PASS`, `BOOTSTRAP_FRESH_DONE=PASS`, `RESULT_RC=0`.
- Запущенный установщик из pinned Git revision: `89de50a074268752fa264972a48e5f697ae55ab8`.

В результате Bootstrap создал только одну задачу №00. Другие инфраструктурные задачи не запускались.

## Evidence: №00 — public catalog

Оператор вручную запустил `00-Обновить список задач` в Semaphore, task execution **#1**:

- `result=PASS`; `source_commit=cdaec56b95c0aa38640e338c28b7519cdc2ad1ac`.
- `created=12`, `unchanged=1`, `updated=0`.
- `infrastructure_tasks_executed=false`.
- Ansible recap: `ok=11 changed=1 unreachable=0 failed=0`.

## Evidence: №05 — GitHub Device Flow

Оператор вручную запустил `05-Получить credentials GitHub` в Semaphore, task execution **#2**:

- GitHub CLI downloaded and extracted successfully.
- Device Flow показал одноразовый код в журнале (сам код **не** переносится в публичную приёмку); задача дождалась browser approval.
- GitHub private repo access verified for `DigitalHouses/dh_ansible_fleet_detail`.
- Private Fleet repo cloned; `secrets/fleet.vault.yml` exists.
- Финальный результат `GITHUB_PRIVATE_FLEET_PASS`.
- Ansible recap: `ok=20 changed=6 unreachable=0 failed=0 skipped=1`.

## Evidence: №06 — one-time Fleet master password

Оператор вручную запустил `06-Ввести master password Fleet Vault` в Semaphore, task execution **#3**:

- Hidden Semaphore secret survey accepted; пароль не отражён в журнале.
- `ansible-vault view` подтвердил корректную расшифровку private Fleet Vault.
- Проверенный master password сохранён в локальный файл с mode `0600`, владелец `semaphore`. Проверка режима и владельца завершилась PASS.
- Финальный результат `FLEET_VAULT_UNLOCKED`, `password_exposed_in_log=false`, `password_saved_for_reboots=true`.
- Ansible recap: `ok=16 changed=2 unreachable=0 failed=0`.

## Что принято и что ещё не проверено

**Принято:** полный цикл с нуля `Bootstrap → №00 → №05 → №06`, кодовая авторизация GitHub, checkout private Fleet и проверенное локальное сохранение master password. Для текущей версии успешный ввод пароля администратором требуется только один раз.

**Не проверено:** перезагрузка LXC и повторное использование сохранённого пароля в новой сессии; идемпотентный повтор №05/06; автоматическое получение конфигурации из Fleet в задачах 10–90; root-workers и любые PVE/API write-операции. Функция `password_saved_for_reboots` является отчётом playbook о локальном сохранении, а не свидетельством фактического reboot E2E.

**Отдельный вопрос:** общий публичный CI ранее останавливался на pre-existing public-boundary violations; успешный live E2E не превращает этот CI в PASS.

**Правило следующего цикла:** действующий CT1231 не удалять повторно без нового адресного разрешения. Исправления вносятся в Git и принимаются через новую закреплённую fresh install revision, а не hotfix внутри работающего LXC.
