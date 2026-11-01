# Site credentials: одно подключение на объект (Ansible Core + Semaphore OSS)

> **SUPERSEDED AS TARGET ARCHITECTURE (2026-11-01):**
> Целевое решение — [ADR-0021](../decisions/0021-semaphore-native-credentials.md):
> одна №05 Obtain Credentials сохраняет весь комплект дома в **Key Store /
> Variable Groups Secrets Semaphore**, №06 выполняет полный Check,
> а все остальные задачи начинают со свежего глобального readiness gate.
> Описанная ниже схема отдельного `site_vault_v1` и внешнего
> Vault-пароля — **промежуточная реализация**, не применять её
> как обязательное условие новых развёртываний.
> Код №05/06 ещё нуждается в миграции и E2E.


**Статус:** нормативная схема для новых объектов; переход старых объектов по отдельной
проверенной миграции. Официальные механизмы: Ansible Vault / `--vault-id`,
SSH public keys / `become` и штатный Semaphore Key Store. Без собственного
менеджера секретов, без коммерческого CI/CD.

## Разделение ролей

| Назначение | Где находится | Защита |
| --- | --- | --- |
| Декларативное описание site | private Fleet `inventories/<site>/` | Git access |
| Конфигурационные секреты и исходные учётные данные | private Fleet `secrets/sites/<site>.vault.yml` | **отдельный Ansible Vault password каждого site** |
| Vault password | у владельца **вне Git/runner**, рабочая копия `/var/lib/semaphore/.dh-credentials/site-vault-password` | файл `semaphore:semaphore 0600` |
| SSH ключи для PVE/managed Linux | создаются на runner при первом разрешённом подключении, на хосте только публичный ключ | уникальные по site, root/authorized user + проверенный host key |
| Semaphore credentials | Key Store (SSH / sudo / Vault), если используется UI-интеграция | шифрование Semaphore, резервная копия БД и ключа |
| Runtime passwords/secrets приложений | выбранные site Vault credentials, при необходимости защищённая рабочая копия на госте | `no_log`, минимальные права |

**SSH private keys не хранить в открытом виде в Git.** Восстановление runner
включает повторный выпуск/регистрацию host access keys (с отзывом прежних),
если они не восстанавливаются из отдельного защищённого backup.
Ansible Vault пароль обязателен для восстановления зашифрованного site secrets;
он должен иметь защищённую копию вне runner (например, в локальном KeePassXC).
Никакого обязательного платного secret manager.

## Структура одного нового site Vault

Внутреннее содержимое **до шифрования**, только пример без реальных secrets:

```yaml
---
site_secrets:
  site_id: serzhan-lab
  credentials:
    pve: {}
    mqtt: {}
    cloudflare: {}
    media: {}
```

В самом Git файл **целиком зашифрован Ansible Vault**, с заголовком
`$ANSIBLE_VAULT`. Не создавать незашифрованный `*.yml` с секретами
в рабочей копии Git.

Для нового объекта — с доверенной машины владельца, в локальном checkout
private Fleet с установленным Ansible Core:

```bash
mkdir -p secrets/sites
ansible-vault create --vault-id serzhan-lab@prompt secrets/sites/serzhan-lab.vault.yml
# Заполнить документ site_secrets, сохранить и выйти из редактора.
# В репозиторий коммитить только зашифрованный *.vault.yml.
git add secrets/sites/serzhan-lab.vault.yml
git commit -m "Initialize encrypted serzhan-lab site credentials"
git push
```

Пароль выбирает и сохраняет владелец, **не вводит его в ChatGPT**.
Каждый site имеет самостоятельный пароль; ID — удобная метка, не пароль.
Перед удалением старого runner проверять, что внешний recovery password
и Git ciphertext существуют и способны расшифроваться.

## Первое подключение нового runner

1. Fresh Bootstrap создаёт один `ansible-<site>` runner и минимальный
   локальный inventory c `site.credentials_profile: site_vault_v1`.
   Никаких секретов из другого дома.
2. №00 открывает Semaphore каталог задач.
3. №05 один раз выдаёт GitHub доступ к private Fleet. Пока общий Fleet
   содержит несколько объектов, **это технически доступ ко всему репозиторию**;
   даже site-separated Vault не скрывает чужие plaintext inventory. Полная
   repository-level изоляция требует будущих приватных репозиториев по домам
   или GitHub App с site-scoped ресурсами.
4. №06 получает пароль в Secret survey, проверяет **именно**
   `secrets/sites/<site>.vault.yml` и соответствие
   `site_secrets.site_id`, затем сохраняет файл пароля `0600` на runner.
   Пока пользователь не создаст encrypted site Vault, №06 возвращает
   `SITE_VAULT_MISSING` и не сохраняет непроверенный пароль.
5. Остальные задачи вызывают одну роль `fleet_credentials`. Она выдаёт
   `fleet_site_credentials` из Vault своего site без нового ввода пароля.
6. При первом инфраструктурном подключении на PVE/managed host регистрируются
   уникальные SSH identities, проверяется host key / TLS, sudo/`become`.
   Последующие задачи переиспользуют эту identity, не получают root заново.

Текущее название поля №06 `fleet_master_password` оставлено ради
совместимости с принятым Semaphore шаблоном и его историей. **Для
`site_vault_v1` оно означает пароль Vault конкретного объекта**, а не
пароль общего Fleet. После безопасной миграции UI можно переименовать.

## Обязательный глобальный Credential Readiness Gate

**Решение владельца:** любой обычный Ansible playbook, независимо от своего
назначения, обязан в текущем запуске проверить **весь набор обязательных
credentials данного site**. Неправильный MQTT-пароль может блокировать
развёртывание медиасервера или PVE: это осознанная эксплуатационная
политика DigitalHouses, более строгая, чем обычный per-task preflight.

- **Состав проверок:** декларативный набор `required` для конкретного
  site, включая credentials всех включённых сервисов. Ненастроенные
  необязательные сервисы не становятся ложными блокерами; исключения
  или `NOT_APPLICABLE` должны быть явными и объяснимыми в site data.
- **Порядок:** проверить присутствие и формат секрета, затем безопасную
  фактическую аутентификацию и необходимые права. Только read-only
  probes, без изменений инфраструктуры. Если право невозможно подтвердить
  без write-операции, показать `UNVERIFIED`, не выдавать `PASS`.
- **Полный отчёт:** проверить каждую независимо проверяемую identity,
  собрать *все* ошибки в одном запуске и только после этого вернуть
  итоговый `READY` или `BLOCKED`. Не завершать отчёт после первой
  ошибки. Если предпосылка отсутствует, зависимые проверки пометить
  `NOT_CHECKED` с причиной.
- **Диагностика:** для каждого credential показывать имя, проверяемый
  сервис/target, статус, краткую безопасную причину и конкретное
  исправление (например, «Obtain Credentials → MQTT»).
  Коды: `PASS`, `MISSING`, `AUTH_FAILED`, `INSUFFICIENT_PRIVILEGES`,
  `UNREACHABLE`, `MISCONFIGURED`, `UNVERIFIED`, `NOT_CHECKED`,
  `NOT_APPLICABLE`. Не путать сетевой сбой с неверным паролем.
- **Граница:** отдельная задача `Check Credentials` показывает полный
  read-only отчёт, а все обычные Deploy/Update/Configure задачи вызывают
  ту же общую Ansible-роль в `pre_tasks` **до первого изменяющего шага**.
  Любой результат, кроме `PASS` и обоснованного `NOT_APPLICABLE` для
  обязательной области, блокирует основной playbook.
- **Исключения против deadlock:** Bootstrap, `Obtain Credentials`,
  `Check Credentials` и специально разрешённые процедуры восстановления
  доступны при состоянии `BLOCKED`. Они не выполняют обычное
  развёртывание в обход gate.
- **Состояние в Semaphore:** история запуска Check фиксирует итог
  `READY`/`BLOCKED` и подробный безопасный отчёт. Прошлый
  `READY` не служит долгоживущим разрешением: каждая новая обычная
  задача выполняет свежую проверку в своём Ansible-запуске.
  Никаких платных Semaphore Workflows или самодельного flag store.
- **Конфиденциальность:** не выводить значения паролей, токены, приватные
  ключи, секретные URL и расшифрованные Vault-данные. Для секретных задач
  использовать `no_log` и безопасный отдельно собранный результат.
- **Критерий приёмки:** тест с двумя независимыми неисправными
  credentials обязан показать **обе** проблемы и способ исправления,
  завершиться `BLOCKED` и доказать отсутствие изменений deploy-ресурсов;
  после исправления повтор становится `READY`, и основной playbook
  продолжает работу без ручного перезапуска отдельной Check-задачи.

Это **требование к будущей реализации**, не утверждение, что глобальный
preflight уже установлен в действующие задачи.

## Совместимость и миграция

На **уже установленных** runner без `site.credentials_profile`
временно применяется `fleet_legacy_v1`: прежний
`secrets/fleet.vault.yml` и `fleet-vault-password`.
Ни один новый Fresh Bootstrap не использует legacy по умолчанию.

Миграция существующего site: выгрузить **только его** secrets из общего
Vault в доверенной среде; создать новый site Vault с новым отдельным
паролем; проверить site_id и восстановление; обновить профиль,
провести read-only E2E и reboot-тест; только затем выводить старый
пароль и прежние credentials из эксплуатации. Не делать `rekey`
общего Vault с риском сломать других клиентов.

## Приёмка

- Положительный тест: только свой `site_id`, пароль `0600`,
  `SITE_VAULT_READY`, успешный прямой Ansible CLI и Semaphore запуск.
- Отрицательные тесты: отсутствующий encrypted Vault; неправильный пароль;
  чужой `site_id`; symlink; непроверенные permissions; пароль другого
  объекта — **все должны завершиться до инфраструктурного apply**.
- Повтор после reboot не требует нового ввода; после пересоздания runner
  восстановление требует только Git + operator-held Vault password +
  повторного SSH enrollment, а не прежнего рабочего диска CT.
- Никаких secret values в stdout, CI, Git commit messages или чате.

## Официальные источники

- Ansible Vault / vault IDs:
  https://docs.ansible.com/ansible/latest/vault_guide/vault_using_encrypted_content.html
- Semaphore Key Store:
  https://semaphoreui.com/docs/user-guide/key-store
- Semaphore file-backed Key Store secrets:
  https://semaphoreui.com/docs/user-guide/key-store/env-and-file-sources
- GitHub Deploy Keys:
  https://docs.github.com/en/authentication/connecting-to-github-with-ssh/managing-deploy-keys
