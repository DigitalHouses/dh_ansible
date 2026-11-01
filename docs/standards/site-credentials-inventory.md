# Site credential inventory — форма №05 · проект v1

**Статус:** инвентаризация и согласование перечня, **НЕ внедрённая рабочая форма**.
Машиночитаемый перечень без паролей: [`catalog/site-credentials.json`](../../catalog/site-credentials.json).
Согласованная модель хранения: [ADR-0021](../decisions/0021-semaphore-native-credentials.md).
Тестовый код (Draft PR #101) сохраняет **только два фиктивных поля**; этот
список не делает готовыми проверку №06 или репозиторий №07.

## Источники и область проверки

- `catalog/semaphore-tasks.json` на `main`: 13 Task Templates,
  №00, №05, №06, №10, №20, №30, №40, №50, №60, №70, №80, №81, №90.
- Исходники и роли `DigitalHouses/dh_ansible` и несекретные YAML
  `DigitalHouses/dh_ansible_fleet_detail/inventories/serzhan-home`,
  `.../serzhan-lab`.
- `DigitalHouses/home-assistant-apps/digitalhouses_pve_agent` и
  `digitalhouses_plex_agent`, в том числе примеры конфигурации.
- Иные HAOS Apps проверены как контекст, **не** как автоматически
  устанавливаемые существующим каталогом Ansible. Самостоятельной
  Semaphore-задачи установки `digitalhouses_pve_agent` пока нет.
  Plex Agent устанавливается **внутри** текущего media deployment
  `roles/media_guest_setup/tasks/plex_agent.yml`, не отдельным
  номером задачи.

## Поля №05 — кандидат для текущего рабочего охвата

**Каждое поле необязательно при повторном запуске.** Пустое значение =
`KEEP`, никогда не delete. В №06 проверяем только credential,
обязательный для **включённой в site manifest функции**. Сервис, явно не
включённый на объекте, получает `NOT_APPLICABLE` вместо выдуманного
пароля. Реальная авторизация проверяется там, где внешний сервис
уже введён в эксплуатацию.

| Группа формы | Поле (идентификатор) | Назначение | Условие | Штатное место |
|---|---|---|---|---|
| GitHub | `github_provision_token` | №07: создать private repo объекта | Первоначальное подключение, до №07 | Variable Group Secret |
| MQTT | `mqtt_username` | Одно имя пользователя брокера для включённых DH-агентов | Используется Plex Agent / PVE Agent | Variable Group, несекретное значение |
| MQTT | `mqtt_password` | Один пароль брокера для включённых DH-агентов | Аналогично | Variable Group Secret |
| Proxmox API | `pve_api_audit_token` | Чтение PVE через API в legacy ролях | **Только** включённые PVE API workflows, не native SSH | Variable Group Secret |
| Proxmox API | `pve_api_write_token` | Изменения PVE через API в legacy ролях | **Только** включённые PVE API workflows | Variable Group Secret |
| Cloudflare | `cloudflare_api_token` | Выполнение разрешённых CF изменений в №10/70 | **Только** при комиссионированной доступной site identity; общий org-wide token автоматически не копировать по домам | Variable Group Secret |

`pve_api_*_token` — переходная совместимость: существуют роли
`roles/pve_audit`, `roles/pve_backup_storage_apply`,
`roles/pve_backup_jobs_apply` и др., использующие эти секреты.
Но нынешние №10/70 и часть PVE задач используют legacy worker /
предварительный commissioning. Запись секрета в №05 **не означает, что
legacy worker автоматически научился читать Semaphore**. Нужна
отдельная миграция потребителя с тестами.

`cloudflare_api_token` аналогично: нынешние
`bootstrap/cloudflare-pve-cutover.py` и
`bootstrap/dh-cloudflare-pve-haos-mtls.py` используют секрет под
`/root/.config/digitalhouses/cloudflare/api-token.secret` на PVE.
Не требуется копировать **общий** Cloudflare org/zone-wide токен
в каждый домашний Semaphore; сперва проверить границы его реальных
полномочий и способ потребления при миграции задач 10/70.

## Что НЕ запрашивать в №05

| Credential | Реальный способ получения | Evidence |
|---|---|---|
| SSH private key доступа к PVE | Создать/commission штатную identity на уровне PVE/site. В форме пароль root не нужен. | `playbooks/audit-pve-native.yml`, `docs/runbooks/pve-native-commissioning.md` |
| Read-only Git deploy key private site repo | Автоматически создать и привязать при №07; отдельный от первоначального токена с правом создания репозитория. | ADR-0021 |
| Пароль владельца Media Server и Samba | `dh-media-accounts.py` создаёт и повторно использует 8-символьный пароль в protected media ZFS; переиспользуют Jellyfin, qBittorrent, Sambee, Filestash и portal. | `roles/media_guest_setup/tasks/file_access.yml`, `.../files/dh-media-accounts.py` |
| API-ключи Radarr, Sonarr, Prowlarr | Генерируются и связываются автоматически после установки; в №05 не вводятся. | `roles/media_guest_setup/tasks/main.yml` (dh-media-arr-wire) |
| Токен Plex API | Plex Agent получает локальную `.LocalAdminToken` через защищённый helper; Plex учётная запись не требуется. | `digitalhouses_plex_agent/README.md`, `roles/media_guest_setup/tasks/plex_agent.yml` |
| Пароль NUT PRIMARY | Создаётся/управляется при конкретном комиссионировании NUT; проверяется на PVE. Отдельного `dh_ups_agent` в текущем каталоге нет: UPS уже включён в PVE Agent. | `digitalhouses_pve_agent/docs/UPS_SHUTDOWN_SETUP.md` |
| Токен Supervisor для HAOS Apps | Предоставляется HAOS штатно; отдельный LLAT в №05 не нужен. | HAOS Apps design, текущий каталог |
| Local Semaphore API credential | Создаётся Bootstrap для своего проекта; не просим пользователя вводить пароль самого Semaphore в его же задачу. | `bootstrap/semaphore-project.py` |

## Отложенные поля — **не добавлять сейчас**

- `home_assistant_long_lived_token`: №70 сейчас только проверяет
  origin `/api/` и 401, без вызова authenticated HA REST.
  Добавлять, когда появится конкретная Ansible-задача, которой он нужен.
- `backblaze_b2_app_key`: в репозитории есть
  `playbooks/set-backblaze-b2-credentials.yml`, однако этот playbook
  отсутствует в опубликованном Semaphore catalog. Конфигурация
  отдельного HAOS Backblaze App не равна её централизованному Ansible
  deployment.
- Credentials сетевого UPS/SNMP — только для объекта с конкретным UPS,
  выбранным методом подключения и внедрённой Ansible задачей.
- Credentials внешних индексаторов: Prowlarr поддерживает пользовательскую
  настройку, но подтверждённого reusable шага ввода через Ansible сейчас нет.

## Контракт расширения

1. Новый playbook / агент должен объявить потребителя **существующего**
   `id` либо обосновать новый credential. Пароли нельзя дублировать
   по номеру задачи или агенту.
2. Добавить его в registry → изменить форму №05 → включить проверку
   именно в общем №06 → применить изменения через №00, с тестом
   сохранности предыдущих значений.
3. `NOT_APPLICABLE` допускается только при явной конфигурации site,
   не как молчаливый fallback на ошибку аутентификации.
4. Не вводить значения в Git, обычные Ansible vars, logs или stdout.
   Реальные site credentials хранить в native Semaphore Key Store /
   Variable Groups Secret.
5. Для `serzhan-lab` сейчас нет полного подтверждённого набора
   `plex_agent.mqtt` в private `media_vm.yml`; включение Plex Agent
   через №80 требует сначала завершить site manifest + credential wiring.
   Нельзя объявить №06 READY только по наличию MQTT полей в Semaphore.

## Что проверять перед слиянием полноценной №05

- Разрешает ли реальный UI Semaphore 2.19.16 >5 optional Secret Survey
  полей (`target: env`) без argv/log раскрытия.
- Пустое значение сохраняет предыдущий секрет; частичная замена MQTT
  не затрагивает Git/PVE/CF.
- API token GitHub не остаётся постоянным организационным admin token
  на домашнем runner после №07.
- Пароль приложения, созданный целевой установкой, не превращается
  в второй несогласованный source of truth.
- Поле/проверка №06 создаются вместе; до полного E2E не считать
  старые №05/06 или Draft PR #101 окончательно внедрёнными.
