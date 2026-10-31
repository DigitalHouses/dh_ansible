# Задача №40 — первое подключение Remote Desktop Commander

**Назначение:** включить постоянный Remote Desktop Commander на уже работающем site runner через штатную задачу Semaphore №40, чтобы AI-агент мог подключаться к своему авторизованному устройству. **Не выдаёт** AI-агенту право выполнять разрушительные операции без отдельного разрешения владельца.

## Обнаруженный prerequisite (2026-10-31)

Fresh Tiny Bootstrap устанавливает только root-owned watcher задачи №00. Шаблон `40-Установить Remote Desktop Commander` из Git-каталога уже существует, но его playbook ожидает запущенный `dh-remote-commander-install.path`. На новом CT1231 этот watcher **не установлен**, поэтому №40 немедленно остановится в preflight. Это не проблема GitHub №05, Vault №06 или reboot.

Штатные файлы для narrowly scoped worker уже находятся в **закреплённом** public Git checkout внутри LXC: `playbooks/apply-runner-remote-commander.yml`, `roles/runner_remote_commander/`, `bootstrap/remote-commander-install-request.sh`, `bootstrap/dh-remote-commander-install.{service,path}`.

## Первый ввод в эксплуатацию

1. На PVE проверить идентичность host и target CT; не удалять и не пересоздавать guest.
2. Внутри только этого CT проверить hostname/site и точный SHA pinned `/opt/digitalhouses/src`.
3. Установить **только штатные root-owned assets** в `/opt/digitalhouses/bootstrap/remote-commander/` и fixed worker `/usr/local/sbin/dh-remote-commander-install`, плюс две systemd units. Никакого `git pull`, изменённого YAML, изменения Semaphore SQLite, sudo для `semaphore` или замены кода на лету.
4. `systemctl daemon-reload && systemctl enable --now dh-remote-commander-install.path`; проверить `systemctl is-active dh-remote-commander-install.path` = `active`.
5. **После этого** инженер запускает задачу №40 в Semaphore. Она создаёт лишь идентифицированный запрос `INSTALL:<uuid>`; root worker из root-owned pinned файлов запускает ровно `playbooks/apply-runner-remote-commander.yml` и устанавливает managed service.
6. Оператор подтверждает показанный в №40 одноразовый код на `https://mcp.desktopcommander.app/device/verify`. Наличие active systemd service — не доказательство успешной авторизации.
7. AI-агент проверяет именно этот актуальный runner в списке подключённых устройств: `Online`, затем выполняет безопасную read-only проверку identity. Только этот критерий означает удалённую доступность.

**Обязательные проверки:** `systemctl is-active digitalhouses-remote-commander.service`, `systemctl is-enabled digitalhouses-remote-commander.service`, `Online` в подключении и успешный read-only вызов от агента. Состояния `SERVICE_ACTIVE`, `CODE`, `CONNECTED` и `Online` различать. Не обещать удалённый доступ до внешней проверки.

Прямое выполнение старого `bootstrap/semaphore-host-install.py` или повтор Tiny Bootstrap ради №40 **не требуется**. Первый root-owned stage должен быть оформлен отдельным повторяемым commissioning-шагом, а не ручным патчем установленного seeder. Это deployment штатных pinned assets, не выдача произвольного root shell непривилегированной Semaphore.

**Операционное правило:** после подключения Remote Desktop Commander AI-агент начинает с [AI agent execution runbook](ai-agent-execution.md), выполняет операции в предоставленных пределах; destructive действия требуют одобрения владельца, а заблокированные подключённым инструментом операции не обходятся.
