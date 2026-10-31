# Задача №40 — Remote Desktop Commander (on-demand)

**Цель:** Remote Desktop Commander устанавливается одной кнопкой **40-Установить Remote Desktop Commander** после первого №00. При этом установка требует заранее размещённого *закреплённого* root-owned watcher, но сам удалённый агент до №40 не стартует.

## Контракт новой версии Bootstrap (ещё НЕ принят live E2E)

Четвёртый этап свежего pinned Tiny Bootstrap под root теперь:
1. создаёт root-owned assets `/opt/digitalhouses/bootstrap/remote-commander/` из того же проверенного Git SHA;
2. размещает fixed-purpose `/usr/local/sbin/dh-remote-commander-install` и два systemd unit `dh-remote-commander-install.{path,service}`;
3. включает **только watcher** `dh-remote-commander-install.path`;
4. выводит `TASK_40_WATCHER=PASS REMOTE_AGENT_INSTALLED=NO`;
5. по-прежнему регистрирует **только №00** через Semaphore. Node.js, npm, Remote Desktop Commander и его сервис **не устанавливаются автоматически**.

Root handler принимает лишь `INSTALL:<uuid>` или `PAIR:<uuid>` от локального `semaphore`, выполняет ровно закреплённый `playbooks/apply-runner-remote-commander.yml`. Semaphore не получает root shell или возможность выбора произвольного playbook.

## Важно: уже установленный CT1231 (2026-10-31)

На принятом ранее CT1231 **этот watcher отсутствует**: журнал №40 показывает `systemctl is-active dh-remote-commander-install.path` → `inactive`/`rc=4`, ручной read-only осмотр показывает `unit not found`.

**№00 меняет только каталог задач через уже установленный fixed worker.** Обновление `dh_ansible` в GitHub, `git pull` или повтор №00 **не может** поставить новый root-owned watcher в работающий CT1231. Это запрет на повышение прав через публичный, изменяемый каталог, а не баг №40. Нельзя лечить выдачей sudo пользователю `semaphore`, редактированием его systemd unit, SQL или подменой root handler.

Для **существующего CT1231** в репозитории реализован **один недеструктивный updater** `bootstrap/upgrade-task40-watcher.sh`, который запускается на PVE root с явными `CTID`, `SITE`, ожидаемыми `GUEST_SHA`, `PVE_CIDR` и `RUNNER_CIDR`. Он проверяет идентичность PVE/CT, не делает `git pull`, скачивает immutable staging code, исполняет его исключительно **в проверенном CT** и ставит worker из уже установленной Git-ревизии. Ни пользовательские secrets, ни PVE/VM lifecycle не затрагиваются.

Путь первичного обновления **не является №00**, потому что старая root capability №00 умеет только catalog sync. После успеха `TASK_40_EXISTING_RUNNER=PASS` запускается штатная №40 в Semaphore. Не нужно удалять LXC или повторять №05/06. Новый updater пока **не проходил live E2E** и должен быть принят по фактическому журналу.

Однократная команда (подставить проверенные параметры конкретного объекта, а не копировать чужие адреса):

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/DigitalHouses/dh_ansible/fca5aaef4560963b0c3f103394421cff0a75ba79/bootstrap/upgrade-task40-watcher.sh) <CTID> <SITE> <INSTALLED_GUEST_SHA> <PVE_CIDR> <RUNNER_CIDR>
```

Без одного из двух разрешённых действий — этого адресного обновления или нового Fresh Bootstrap — №40 на старом госте остаётся BLOCKED. **Действующего разрешения на уничтожение CT1231 нет и оно не требуется для updater.**

## Штатный пользовательский путь на подготовленном runner

1. Bootstrap подтверждает `TASK_00_ONLY=PASS`, `TASK_40_WATCHER=PASS`, `REMOTE_AGENT_INSTALLED=NO`.
2. Оператор запускает №00 через Semaphore; №40 появляется среди других шаблонов.
3. Оператор запускает №40: Semaphore помещает проверенный запрос, root worker устанавливает фиксированную версию Node.js и Remote Desktop Commander, создаёт managed `digitalhouses-remote-commander.service`.
4. Оператор подтверждает одноразовый код на `https://mcp.desktopcommander.app/device/verify`.
5. AI-агент находит **текущий** runner в своём подключении, видит `Online` и самостоятельно выполняет разрешённый read-only вызов. `SERVICE_ACTIVE` не означает `Online`.

## Приёмка

- Bootstrap из опубликованного pinned SHA: `CODE_GIT_SHA=PASS`, `TASK_00_ONLY=PASS`, `TASK_40_WATCHER=PASS`, `REMOTE_AGENT_INSTALLED=NO`, `RESULT_RC=0`.
- Запуск №00, затем №40: фиксированный worker `PASS`, systemd agent `active/enabled`; display device-code или `ALREADY_AUTHORIZED`.
- Независимое подтверждение `Online` через действительное Remote Desktop Commander подключение, read-only вызов агентом, повтор №40 не меняет необходимые конфигурации.
- Пока нового живого Fresh E2E и удалённого `Online` нет — статус **IMPLEMENTED IN GIT / NOT LIVE ACCEPTED**.

Для рамок самостоятельных операций AI-агентов см. [основной runbook](ai-agent-execution.md) и [AGENTS.md](../../AGENTS.md). Подключение к runner не выдаёт дополнительных прав на PVE/production или destructive операции.
