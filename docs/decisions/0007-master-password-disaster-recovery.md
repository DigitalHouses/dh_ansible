# ADR-0007 — One master password and Git-based secrets disaster recovery

Статус: **Accepted**. Дата: 2026-10-06.

## Контекст

Local runner является расходным компонентом. Его диск, LXC или весь PVE может
быть потерян. Архитектура, в которой API tokens, SSH keys или другие
инфраструктурные credentials существуют только на runner, делает восстановление
зависимым от погибшего диска и неприемлема для эксплуатации DigitalHouses.

## Решение

Для fleet действует одна модель восстановления:

> **new runner + Git repository + one operator-held master password =
> восстановление site configuration и сохранённых инфраструктурных credentials.**

Git является source of truth одновременно для:

- declarative configuration;
- inventories;
- encrypted fleet credentials;
- recovery metadata, которое безопасно хранить в repository.

Текущий стандарт хранения infrastructure secrets:

```text
secrets/
└── fleet.vault.yml
```

`secrets/fleet.vault.yml` является **единственным tracked encrypted
credential container fleet** и должен содержать только Ansible Vault
ciphertext.

Привязка credential к конкретному объекту хранится **внутри encrypted data**,
а не в имени файла или каталоге:

```yaml
fleet_secrets:
  sites:
    <site>:
      pve: ...
      truenas: ...
```

Runner выбирает только `fleet_secrets.sites[site_id]`. Отдельные
`secrets/<site>/...` запрещены.

## Master password

Используется один master password, который находится у владельца/оператора DigitalHouses.

Master password:

- минимальная длина — 8 символов; максимальная длина не ограничивается;
- не хранится в Git;
- не должен зависеть от существования конкретного runner;
- используется для расшифровки tracked Vault secrets при bootstrap/recovery;
- является единственным обязательным человеческим secret для восстановления
  конфигурации и сохранённых credentials из Git.

Нельзя создавать отдельный обязательный Vault password на каждый дом, если это
ломает этот recovery contract.

## Что обязано находиться в encrypted Git

Любой secret, потеря которого при уничтожении runner затруднит штатное
восстановление site, должен иметь authoritative encrypted copy в Git, включая
по мере появления соответствующих capabilities:

- PVE audit API token secret;
- PVE write API token secret;
- capability-scoped PVE token secrets;
- TrueNAS API credentials;
- managed Linux SSH private material, если ключ должен быть сохранён, а не
  перевыпущен;
- application/service passwords и API keys, если они являются частью managed
  infrastructure и должны быть восстановлены;
- другие site-specific infrastructure credentials.

Имена переменных могут развиваться вместе с roles, но правило хранения не
меняется.

## Что может быть только локальным

Только данные, которые штатно и безопасно **регенерируются**, могут не иметь
authoritative copy в encrypted Git.

Пример: read-only Git deploy key local runner. После потери runner новый deploy
key создаётся заново и регистрируется в GitHub; старый ключ не нужен для
восстановления infrastructure state.

Обязательный infrastructure secret никогда не должен иметь единственной копии на runner или другом потенциально теряемом диске.

Runtime plaintext, decrypted temporary files, caches и session material на
runner:

- допустимы только когда нужны конкретному runtime workflow;
- не являются backup;
- не являются source of truth;
- могут быть уничтожены вместе с runner без потери возможности recovery.

## Recovery flow

После потери runner:

1. Создать новый runner по общему bootstrap profile.
2. Получить read-only доступ к fleet Git repository.
3. Checkout требуемой immutable revision.
4. Оператор вводит единый master password.
5. Расшифровать `secrets/fleet.vault.yml` и выбрать namespace требуемого `site_id`.
6. Восстановить site-scoped API/SSH/application credentials и managed
   configuration общими roles/playbooks.
7. Выполнить read-only audit и reconcile до возобновления write operations.

Backup самого runner для сохранности credentials **не требуется**.

## Repository guardrails

- `.gitignore` не должен исключать `secrets/fleet.vault.yml`.
- CI обязан отклонять `secrets/fleet.vault.yml`, если файл не начинается с
  Ansible Vault header `$ANSIBLE_VAULT;`.
- tracked credential files, явно привязанные к site в имени/пути
  (`secrets/<site>/...`), запрещены.
- plaintext secrets запрещены в Git.
- master password запрещён в Git.
- наличие encrypted secrets в Git является ожидаемым поведением, а не security
  exception.

## Fleet rule

Дом отличается данными, а не кодом. Site-specific credentials являются
namespaced data внутри единого encrypted fleet Vault и используются общими
roles/playbooks. Новый дом не создаёт новый credential-container.


## Legacy bootstrap Vault migration

Bootstrap Vault-файлы, созданные до принятия ADR-0007, являются только
migration source и не задают master password. Они могут быть whole-file Vault
или YAML с inline `!vault`, а их прежний local vault password может отличаться
от принятого operator-held master password.

При переходе на ADR-0007:

- master password подтверждается оператором двойным скрытым вводом;
- новый tracked `secrets/fleet.vault.yml` шифруется этим master password;
- legacy secrets при необходимости расшифровываются их прежним локальным
  механизмом и переносятся в новый tracked Vault;
- команда `ansible-vault view` не используется как универсальная проверка
  YAML-файлов с inline `!vault`;
- после подтверждённой миграции legacy local vault/password material выводится
  из эксплуатации.
