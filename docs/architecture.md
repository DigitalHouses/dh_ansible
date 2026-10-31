# DigitalHouses infrastructure as code

The project contains **one generic codebase**, not per-site implementations.

## Repository boundary

- `DigitalHouses/dh_ansible` — reusable Ansible roles, modules, playbooks, Semaphore bootstrap, synthetic tests, CI and public-safe documentation.
- `DigitalHouses/dh_ansible_fleet_detail` — **private**, data-only: `inventories/<site>/` and encrypted `secrets/fleet.vault.yml`. No executable source or GitHub Actions workflows.

A site differs by declarative data, **not code**.

## Runner / deployment

A site-local Debian LXC runner provides Semaphore and Ansible. Installers pin two immutable Git commits independently. Owner authorization is required only for private Fleet Detail. Semaphore uses a credential-free HTTPS repository for public code and a separate read-only SSH repository for site inventory. General playbooks use `inventory_dir`, while the CLI wrapper uses `DH_FLEET_DETAIL_DIR`.

No user credentials or site inventories may enter public CI logs, fixtures, artifacts or repository history. The encrypted Fleet Vault is *still sensitive*: it permits offline password guessing and does not in itself enforce site-scoped ciphertext access.

## Tiny Bootstrap → LXC → Semaphore: утверждённый контракт (2026-10-31)

**Одна команда на чистом Proxmox VE, всего четыре этапа:**

1. **LXC:** маленький Bash `bootstrap/install.sh` скачивает официальный Debian 13 (если отсутствует), создаёт на PVE отсутствующий unprivileged runner LXC с обычным `pct`, корректирует /etc mode при необходимости и запускает CT. Никаких изменений соседних VM/LXC.
2. **Git:** в только что созданном LXC устанавливается Git, клонируется `DigitalHouses/dh_ansible` и проверяется pinned Git SHA. **Публичный код не требует GitHub OAuth**.
3. **Ansible + Semaphore:** уже существующий гостевой установщик `bootstrap/semaphore-guest-install.py` создаёт изолированный Ansible runtime и Semaphore HTTPS внутри LXC. **На PVE Ansible не устанавливается**.
4. **Задача №00:** `bootstrap/task-00-setup.sh` создаёт только минимальный локальный inventory runner, использует существующий Semaphore API seeder для регистрации **ровно одной** задачи `00-Обновить список задач` и запускает ограниченный каталоговый watcher.

**Строгая граница:** Bootstrap не получает private Fleet, не восстанавливает Vault/PVE identities, не переносит credentials, не разворачивает Media Server и не устанавливает worker для задач 10–90. Эти действия относятся к отдельно авторизованным задачам после установки.

**Передача управления:** задача №00 **не запускается автоматически**. Оператор запускает её **через Semaphore**, после чего она получает актуальный каталог из GitHub, привязывает его к проверенному revision и синхронизирует остальные шаблоны (без автозапуска). История и секреты уже имеющегося Semaphore не удаляются. Все дальнейшие инфраструктурные задачи запускаются из Semaphore по их стандартным правилам.

**Fresh E2E:** до приёмки на Jonsbo тестовый CT1231 удаляется между неудачными прогонами только после проверки идентичности и явного разрешения; VM2253 HAOS и другие гости неприкосновенны. Установщик **никогда не удаляет уже существующий CT**, не поддерживает resume и завершает работу с ошибкой при конфликте VMID. Приёмка: `RESULT_RC=0`, `BOOTSTRAP_FRESH_DONE=PASS`, `CT_CREATE=PASS`, `SEMAPHORE_INSTALL=PASS`, `TASK_00_ONLY=PASS`, доступный HTTPS и отдельный успешный запуск №00 из интерфейса Semaphore. Публикация файлов в GitHub не эквивалентна полному E2E.

## Safety

Audit → plan → explicitly approved apply. No hidden guest recreation, mutable unpinned releases or production cutovers. Every site-specific operation must first verify site identity and declared scope. Home runner pilot acceptance precedes any production migration.

**Status:** private staging. Public visibility is forbidden until a clean Git history, complete secret scan, CI, and site E2E acceptance.
