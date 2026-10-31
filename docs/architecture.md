# DigitalHouses infrastructure as code

The project contains **one generic codebase**, not per-site implementations.

## Repository boundary

- `DigitalHouses/dh_ansible` — reusable Ansible roles, modules, playbooks, Semaphore bootstrap, synthetic tests, CI and public-safe documentation.
- `DigitalHouses/dh_ansible_fleet_detail` — **private**, data-only: `inventories/<site>/` and encrypted `secrets/fleet.vault.yml`. No executable source or GitHub Actions workflows.

A site differs by declarative data, **not code**.

## Runner / deployment

A site-local Debian LXC runner provides Semaphore and Ansible. Installers pin two immutable Git commits independently. Owner authorization is required only for private Fleet Detail. Semaphore uses a credential-free HTTPS repository for public code and a separate read-only SSH repository for site inventory. General playbooks use `inventory_dir`, while the CLI wrapper uses `DH_FLEET_DETAIL_DIR`.

No user credentials or site inventories may enter public CI logs, fixtures, artifacts or repository history. The encrypted Fleet Vault is *still sensitive*: it permits offline password guessing and does not in itself enforce site-scoped ciphertext access.

## Tiny Bootstrap → Ansible → Semaphore: утверждённый контракт (2026-10-31)

**Одна команда на чистом Proxmox VE.** Bootstrap — минимальная доверенная точка входа, **не** второй движок provisioning на Bash/Python. Его обязанности ограничены проверкой источника/зафиксированного Git revision, идентичности site/PVE, безопасной авторизацией владельца и получением Fleet Vault, установкой минимального изолированного Ansible control environment на PVE и запуском **одного** Ansible bootstrap playbook. Секреты не помещаются в URL, аргументы процессов или логи.

**Всё остальное — Ansible:** декларативно создать защищённый site runner LXC; установить и проверить в нём Ansible, Semaphore HTTPS, Bonjour/mDNS, необходимые identities и доступ к закреплённой версии Git/Fleet; выполнить acceptance. Код общего развёртывания находится в Ansible roles/playbooks, **не** в разрастающемся Python-скрипте, списках вручную передаваемых worker assets, site-specific исключениях или отдельном shell-orchestrator.

**Первое состояние Semaphore после Bootstrap — ровно одна задача №00 «Обновить список задач».** Она установлена и доступна, но **не запускается автоматически**. В Bootstrap нет каталога остальных задач и нет автоматического запуска задач 10–90. Пользователь запускает №00 **в Semaphore**; только она получает и проверяет актуальный каталог из GitHub, связывает его с определённым Git commit, синхронизирует задачи и сохраняет историю и пользовательские секреты. Далее задачи выполняются через Semaphore.

**Fresh E2E на Jonsbo до приёмки:** после любого неуспешного запуска тестовый CT1231 на PVE `192.168.11.6` удаляется по явному решению владельца и строгой проверке identity, следующий запуск начинается без resume. HAOS VM2253 и другие гости неприкосновенны. Приёмка требует успешного завершения **именно новой минимальной архитектуры**, независимых постусловий LXC/Ansible/Semaphore и возможности выполнить №00 из UI. Уже работающий legacy Python Bootstrap **не считается** реализацией этого контракта; без фактической реализации и теста нельзя объявлять переход завершённым.

## Safety

Audit → plan → explicitly approved apply. No hidden guest recreation, mutable unpinned releases or production cutovers. Every site-specific operation must first verify site identity and declared scope. Home runner pilot acceptance precedes any production migration.

**Status:** private staging. Public visibility is forbidden until a clean Git history, complete secret scan, CI, and site E2E acceptance.
