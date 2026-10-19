# ansimble 2.0 — обязательные инженерные принципы v1.0

Статус: проектный стандарт. Дата: 2026-10-05. При конфликте предложения реализации с этими принципами сначала обсуждается изменение принципа, а не создаётся исключение молча.

## I. Архитектура

### P01. Declarative first

Git описывает **желаемое состояние**, а не историю действий. Например, `users:` содержит требуемые identities и свойства, но не команды создания каждого пользователя. Указываем управляемые и защищённые поля; не переписываем неуправляемые свойства.

### P02. One implementation per resource type

Единый контракт для users, groups, datasets, ACL, shares. **Запрещён паттерн «один script + preflight + verifier + tests на одного пользователя/объект».** Новый экземпляр ресурса добавляется изменением декларативных данных, а не написанием программы. Однотипные проверки агрегируются.

### P03. Roles/modules first

Приоритет: поддерживаемые Ansible modules/collections → переиспользуемая роль → обоснованный custom module. Bash/Python только при отсутствующих API/модулях, аппаратном bootstrap, независимой диагностике или recovery. Никакого «скрипта для скрипта».

### P04. One codebase, multiple sites

Один набор roles и playbooks, отличия только в inventory/site vars/Vault. Site-specific код — исключение, требующее доказанной аппаратной или API-специфики.

### P05. KISS, explicit complexity budget

Не создавать собственный orchestration framework, state DB, DSL, очередь задач, web backend, Kubernetes и подобные зависимости без измеримой необходимости. Предпочитать очевидные штатные средства Ansible.

## II. Управление инфраструктурой

### P06. Adopt before provision

`adopt` принимает существующее состояние без пересоздания и стирания. `provision` создаёт отсутствующее, если явно разрешено. Существующие UID/GID, VMID, GUID, MAC, ACL и другие identity-поля нельзя переназначать ради шаблона. Чужие/неизвестные ресурсы не захватывать.

### P07. Idempotency by design

Повторный штатный apply на согласованном состоянии — `changed=0`. Одноразовые/неидемпотентные операции выделяются и имеют защищённые условия повторного запуска. Проверять идемпотентность на resource type, а не по одному скрипту на каждую identity.

### P08. Standard lifecycle

`audit` — только live read-only факты; `plan` — достоверный diff, impact и risk с привязкой к точному Git commit; `apply` — только явно допущенные изменения; `verify` — независимые postconditions; `status` — read-only drift/health. Ansible `--check` — вспомогательная техника, не универсальный эквивалент реального плана.

### P09. Risk-proportional change control

- **Routine:** безопасные, обратимые стандартные обновления — автоматически после CI и утверждения общей политики.
- **Sensitive:** сеть, ACL, право доступа, VM configuration — подтверждение конкретного плана и preconditions.
- **Destructive:** удаление данных/снимков/гостей, wipe, recursive ACL reset, SATA/PCI — отдельное явно выраженное адресное разрешение с проверяемым планом восстановления.

Глобального автоматического rollback нет. Если ответ API неопределённый: **UNKNOWN → stop writes → read-only reconciliation**. Не делать blind retry; частичный успех не объявлять failure или success без проверки.

## III. Качество и безопасность

### P10. Test before deploy

CI проверяет форматирование, lint, syntax, unit, контрактные и offline integration tests на доступных сценариях. Там, где возможно, проверяем повторный apply/`changed=0` в изолированной тестовой среде. Реальные аппаратные/PVE/TrueNAS postconditions — только site acceptance отдельно; CI без производственных credentials. Не выдавать placeholder тесты за полноценные интеграционные.

### P11. Secure by default

SSH pinned host keys для Linux; официальные API с проверкой TLS и server identity для PVE/TrueNAS; least privilege; изоляция site credentials, Vault/зашифрованные секреты; secret-safe logging. Никогда не отключать TLS verification для обхода диагностики без специального решения.

### P12. Git as source of truth and disaster-recovery source

Изменения проходят через version-controlled desired state. `plan` и `apply` используют одну immutable revision. Локальные site runners ограничены своим объектом, получают код через read-only Git access. Документируем ручные emergency changes и проводим reconcile перед следующим apply.

Git также является source of truth для **зашифрованных fleet secrets**, необходимых для восстановления инфраструктуры. Обязательный эксплуатационный secret не может существовать только на local runner, PVE, TrueNAS или другом потенциально теряемом диске.

Нормативная DR-модель:

> новый runner + Git repository + один operator-held master password = восстановление site configuration и всех сохранённых infrastructure credentials из единого fleet Vault.

Master password не хранится в Git и не зависит от конкретного runner. Credential container не привязывается к имени site в filesystem path; site принадлежность задаётся только namespace внутри encrypted fleet Vault. Plaintext/runtime copies на runner являются только рабочим состоянием и не считаются backup/source of truth. Подробный контракт — ADR-0007.

## IV. Эксплуатация

### P13. Detect drift, don't hide it

Регулярный read-only drift detection. Различать managed drift, unmanaged resource, intended override, unknown state. Не проводить silent auto-remediation, пока scope и safety не подтверждены. Название GitOps применять только там, где действительно реализовано непрерывное reconciliation.

### P14. Reduce toil, not multiply it

Повторяемые простые операции выполняются **пакетно**, с переиспользуемыми проверками и одной автоматической приёмкой. Если новая простая функция потребовала отдельной цепочки Bash/Python/wrappers и ручной проверки каждой операции, это **архитектурный дефект**, а не повышенная надёжность. Исправляем abstraction boundary, не дописываем ещё один guard.

### P15. Maintainability and architectural dissent

Проект должен оставаться понятным новому инженеру. Код/технические имена на английском, документация и operator reports на русском. AI-архитектор обязан своевременно предупреждать о неоправданной сложности, дублировании, рисках и ошибочных направлениях.

## Проверка решения (acceptance для нового функционала)

Новая функция допускается, если одновременно выполняются следующие условия:

1. Новый пользователь/dataset/share на втором объекте **не требует изменения роли**.
2. Нет отдельного writer для конкретного имени ресурса и нет конкурирующего API-механизма.
3. План отражает реально возможный diff и явно показывает неизвестные/опасные операции.
4. Повторный apply даёт `changed=0` для уже совпадающего состояния.
5. Автотесты покрывают тип ресурса и пограничные сценарии (existing, absent, drift, uncertain write, API error).
6. Проверяемые postconditions подтверждают реальное состояние, а не только код возврата процесса.
7. Внешние секреты, legacy и unmanaged ресурсы не затронуты.

Ссылки на первичные практики:
- https://docs.ansible.com/ansible/latest/playbook_guide/playbooks_reuse_roles.html
- https://docs.ansible.com/ansible/latest/playbook_guide/playbooks_checkmode.html
- https://opengitops.dev/
- https://sre.google/sre-book/eliminating-toil/
