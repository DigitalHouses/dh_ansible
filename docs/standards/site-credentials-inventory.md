# Site credentials — форма №05 · v1

**Статус:** согласованная корректировка перечня (чистый PVE = root + пароль), Draft PR #101; ещё не принято UI E2E. Реальных credentials нет в Git.

Source of truth для полей: [`catalog/site-credentials.json`](../../catalog/site-credentials.json).
Архитектура: [ADR-0021](../decisions/0021-semaphore-native-credentials.md).

## Область аудита

- `dh_ansible/catalog/semaphore-tasks.json`, роли, playbooks, bootstrap;
- разрешённые несекретные inventory `dh_ansible_fleet_detail`: `serzhan-home`, `serzhan-lab`;
- `home-assistant-apps/digitalhouses_pve_agent`, `digitalhouses_plex_agent` и их публичная документация.

В опубликованном каталоге есть 13 templates (№00, 05, 06, 10, 20, 30, 40, 50, 60, 70, 80, 81, 90). Отдельной Semaphore-задачи для PVE Agent пока нет; Plex Agent ставится внутри `roles/media_guest_setup`.

## Пять полей формы №05

Все поля для ввода необязательны при **повторном** запуске. Пустое поле = `KEEP`. №06 отдельно сообщает, какие credentials нужны текущему site. Credential одной услуги не дублируется в полях других задач/агентов.

| Группа | Поле | Хранение в Semaphore | Потребитель |
|---|---|---|---|
| GitHub | `github_provision_token` | Variable Groups → Secrets | №07: первоначально создать private repository объекта; последующее чтение через сгенерированный site-only deploy key |
| MQTT | `mqtt_username` | Variable Groups → обычная переменная | Общий логин для включённых DH PVE Agent / DH Plex Agent |
| MQTT | `mqtt_password` | Variable Groups → Secrets | Общий пароль тех же агентов |
| **Proxmox VE** | **`pve_root_password`** | **Key Store → Login with Password**, фиксированный login=`root` | Первоначальная авторизация чистого PVE и подключение управляемых узлов; для PVE HTTPS API используется `root@pam` |
| Cloudflare | `cloudflare_api_token` | Variable Groups → Secrets | №10/70 после отдельного переноса потребителя на штатное Semaphore storage |

**Ни audit, ни write API-токенов PVE в форме №05 НЕТ.** Для чистого PVE пользователь вводит один пароль `root` — не два токена и не повторный пароль для каждого playbook. У Proxmox API имя той же системной учётной записи — `root@pam`; проверку сделает №06 через PVE authentication ticket по HTTPS с проверенным сертификатом, когда этот шаг будет реализован. Парольный SSH root может быть запрещён, поэтому успешный `root@pam` **не доказывает** готовность SSH. Подключение SSH-ключа выполняется отдельно при commissioning и тестируется независимо. Пароль не выводится в Git/лог/CLI.

## Что не запрашиваем в №05

| Credential | Почему не вводится |
|---|---|
| PVE API audit/write tokens | Для **новой** установки выбран root password; нет требования создавать API-токены заранее. Существующие legacy API-ролей до миграции могут остаться BLOCKED |
| PVE SSH private key | Генерируется/commissioned для объекта, а не присылается пользователем; не подменяет ввод root password на первичном шаге |
| Git deploy key | Генерируется и подключается при №07 после создания site private repository |
| Media owner/Samba/Jellyfin/qBittorrent/Filestash/Sambee пароли | `dh-media-accounts.py` генерирует и повторно применяет локальный пароль на ZFS |
| Radarr/Sonarr/Prowlarr API keys | Генерируются на VM, связываются `dh-media-arr-wire` |
| Plex API token | Plex Agent берёт локальную `.LocalAdminToken` штатным защищённым helper |
| NUT PRIMARY пароль | Создаётся/commissioned совместно с конкретным NUT/UPS; мониторинг UPS часть DH PVE Agent, а не отдельный `dh_ups_agent` |
| HAOS Supervisor token | HAOS Apps используют штатную авторизацию Supervisor |
| Semaphore local API | Bootstrapped внутри доверенного runner LXC; не требуется вводить в собственную форму |

## Когда расширять перечень

- `home_assistant_long_lived_token`: сейчас нет задачи каталога с аутентифицированным HA REST API.
- Backblaze B2: playbook есть в репозитории, но не опубликован в текущем каталоге.
- SNMP network UPS: только когда внедряем конкретный сетевой UPS с нужной аутентификацией.
- Дополнительные внешние индексаторы: когда появится подтверждённый reusable Ansible consumer.

## Состояние потребителей и ограничения текущей миграции

Часть старых PVE и Cloudflare задач зависит от legacy `root-worker`, приватного Fleet Vault или прежних путей секретов. **Одной записью root-пароля в Key Store они автоматически не превращаются в рабочие задачи.** При обновлении каждого потребителя убрать зависимости от PVE API токенов и использовать root@pam/commissioned SSH согласно принятой модели. Не создавать параллельное обязательное хранилище.

У `serzhan-lab` ещё отсутствуют подтверждённые полные настройки Plex Agent MQTT в исходном private `media_vm.yml`; добавление общих MQTT полей в №05 не завершает автоматически deploy.

## Acceptance перед слиянием

1. Реальный Semaphore 2.19.16 Task UI принимает пять optional полей с `target: env` без экспозиции секретов в argv, task log, re-run defaults.
2. Key Store создаёт ровно одну PVE root запись с login=`root`; повторный №05 с одним новым паролем использует `override_secret` и **не меняет** MQTT/CF/Git.
3. Смена только MQTT password не трогает PVE root Key Store.
4. №06 проверяет сохранение и реальную `root@pam` авторизацию; SSH проверяется отдельно и не предполагается разрешённым по паролю.
5. №07 создаёт private site repo после №06, не записывает org-admin credentials в обычный site runtime.
6. Действующие ранее введённые credentials и ID шаблонов сохраняются при миграции №00.
7. База Semaphore и `access_key_encryption` восстанавливаются из backup; все значения остаются защищены.

**Draft PR #101 не переносить в production `main` до реальной UI приёмки и перехода зависимых задач.**
