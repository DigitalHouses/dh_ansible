# DigitalHouses · DH Ansible

Публичный универсальный код инфраструктуры DigitalHouses: Ansible, роли, playbooks, Semaphore, Bootstrap и проверяемый установщик.

## Архитектура — два рабочих репозитория

| Репозиторий | Доступ | Назначение |
| --- | --- | --- |
| `DigitalHouses/dh_ansible` | PUBLIC | Весь универсальный исполняемый код, Bootstrap, тесты, CI и документация |
| `DigitalHouses/dh_ansible_fleet_detail` | **PRIVATE** | Инвентаризации объектов и зашифрованный Ansible Vault, без исполняемого кода/Actions |

«Дом отличается данными, а не кодом». Реальные site inventory, Vault, пароли, ключи и данные домов не публикуются в этом репозитории.

## Единый установщик

Исходник единой точки входа находится в **`bootstrap/install.sh`** этого же репозитория. Отдельный старый публичный репозиторий installer сохранён временно только для отката до полного E2E; он не является частью целевой схемы.

Установщик принимает `<owner>-<type>` и необязательный контролируемый режим `--resume`, `--resume-fresh`, `--resume-ansible` или `--resume-semaphore`. Пример с синтетическим site ID для **чистого тестового PVE** (перед любым запуском требуется отдельное разрешение):

```bash
clear
INSTALLER_REF="main"
SOURCE="$(curl -fsSL --retry 2 "https://raw.githubusercontent.com/DigitalHouses/dh_ansible/${INSTALLER_REF}/bootstrap/install.sh")"
test -n "$SOURCE" || exit 2
bash -c "$SOURCE" -- "demo-office"
```

**Воспроизводимая приёмка:** вместо изменяемого `main` указывать полностью проверенный SHA коммита с `bootstrap/install.sh`. Внутри установщика отдельно закреплены `CODE_RELEASE`, Git blob SHA `bootstrap/semaphore-host-install.py`, SHA256 GitHub CLI и `FLEET_RELEASE`. Проверка приватного Vault и идентичности выбранного site выполняется до любого создания LXC; GitHub Device Login владельца остаётся на PVE. Общий Fleet Vault и GitHub credentials на runner не передаются.

## Статус

Доступность и CI публичного кода проверены, но Bootstrap E2E **не принят**. Уже существующий CT1231 не соответствует новому каноническому hostname/site ID и **не должен удаляться или принудительно усыновляться**. До явного отдельного разрешения недопустимы удаление PVE CT/VM, шаблонов Debian, переразметка дисков, изменения production и удаление архивных репозиториев.

Полное принятие: проверенный PVE preflight → владелец/приватный Fleet/Vault → чистая установка runner → HTTPS Semaphore/API → site-local inventory → read-only smoke → повторяемость/идемпотентность без drift.
