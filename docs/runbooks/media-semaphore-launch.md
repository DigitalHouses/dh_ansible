# Media Server: запуск Semaphore и прямой инженерный fallback

**Область:** задача `80-Медиасервер · Развёртывание`, runner Ansible/Semaphore и защищённый PVE worker.  
**Правила:** [AGENTS.md](../../AGENTS.md), [стандарт Semaphore](../standards/semaphore-task-design.md), [инженерные принципы](../engineering-principles.md).  
**Статус:** подтверждённый способ исполнения на пилотном site runner; **не** универсальное разрешение на операции с production.

## Зачем это документировано

28 октября 2026 г. инженерский запуск `playbooks/plan-pve-media-vm.yml` **без создания Task в API Semaphore** был выполнен непосредственно на runner от UNIX-пользователя `semaphore`. Зафиксированы:

- Локальный журнал `/var/log/dh-media-direct-4a39285-20261028.log` на пилотном runner: `PLAY RECAP runner ok=44 failed=0`, `result: DEPLOYED`.
- Защищённый worker исполнил развёртывание и проверку гостя; успешность должна дополнительно подтверждаться live-состоянием и независимой приёмкой, а не только recap.
- Позднее UI Task #67 дошла до настройки Plex и упала на проверке XML `Preferences.xml`; это **ошибка приложения/Ansible-кода**, не ошибка метода запуска.
- 30 октября 2026 г. попытка создать новую Task через локальный Semaphore API/запустить тот же playbook удалённым инструментом была **заблокирована на уровне инструмента**. Такая блокировка **не доказывает**, что у runner отсутствуют права или что Semaphore неисправен. Не утверждать `запустить самостоятельно невозможно` без проверки истории и механизмов.

Исторический журнал — **только доказательство существования пути**, не готовое основание повторять прежние параметры, Git SHA, destructive-политику или правовые полномочия.

## Порядок действий AI-агента или инженера

1. **Сначала live-state.** Проверить, нет ли уже выполняющейся Task №80, другого прямого Ansible-процесса или worker-запроса. Сверить VMID, VM status, существующий pool GUID и WWN, NFS/backup-зависимости, а также явно утверждённые `media_vm_action` и `media_raw_disk_action`. Не запускать второй write параллельно.
2. **Сначала штатная точка входа.** Если доступен разрешённый механизм создания Task через интерфейс или штатный API Semaphore, применять шаблон №80 с двумя независимыми survey-параметрами. Не пытаться подменить UI-запуск записью в SQLite.
3. **Если API-запуск недоступен, проверить проверенный прямой путь.** Запускать **тот же** `playbooks/plan-pve-media-vm.yml` на авторизованном site runner от локального UNIX-пользователя `semaphore`, через установленный Ansible virtualenv, с точным site inventory и pinned checkout. Он сам делает `audit → plan → approved apply → verify` через root-owned watcher. **Не** выполнять `dh-pve-media-deploy` напрямую, не читать/передавать root-only SSH-ключи в `semaphore`, не обходить nonce/SHA-проверки.
4. **Любая блокировка безопасности удалённого инструмента — STOP.** Не обходить отказ через иной инструмент, shell-обёртку, API-токен, ручную запись worker-request или повышенные права. Отдельно сообщить, что блокируется: API-вызов, shell launch, авторизация OS или сам playbook. Пользователю можно предложить штатный Run из Semaphore.
5. **После запуска** зафиксировать exact checkout Git SHA, site, approved actions, способ запуска, путь к журналу, nonce и результат root-worker; проверить реальную VM/ZFS/GPU и отдельно выполнить acceptance №81. Прямой запуск **не создаёт запись Task в Semaphore** и его нельзя называть «Task #NN Success» без соответствующей записи.

## Минимальный прямой запуск (только при разрешённом доступе)

Ниже **операторский шаблон**, а не команда, разрешающая очистку диска. Его можно применять исключительно после подтверждения целевого site, явного approval для выбранных действий и отсутствия другого запуска. Значения `SITE_ID`, `CODE_DIR`, `VM_ACTION`, `DISK_ACTION` берутся из **проверенного live-state и утверждённого Fleet**, а не из сохранённого примера.

```bash
# На авторизованном runner под root (команды Ansible исполняются от semaphore).
set -euo pipefail

SITE_ID="<verified-site-id>"
CODE_DIR="/var/lib/semaphore/<verified-pinned-checkout>"
VM_ACTION="keep"       # create | keep | recreate; только явно утверждённый выбор
DISK_ACTION="import"   # import | erase; erase требует отдельного approval
INV="/var/lib/ansible/site-data/$SITE_ID/hosts.yml"
LOG="/var/log/dh-media-direct-$(date -u +%Y%m%dT%H%M%SZ).log"

test -f "$INV"
test -f "$CODE_DIR/playbooks/plan-pve-media-vm.yml"
test -d "$CODE_DIR/roles"
test "$(runuser -u semaphore -- git -C "$CODE_DIR" rev-parse HEAD | wc -c)" -eq 41
# До apply независимо проверить: отсутствие параллельного запуска, Fleet
# identity/approval, совпадение checkout SHA с staged worker release SHA.
# Эти проверки обязательны; строки выше их НЕ заменяют.

(
  cd "$CODE_DIR"
  runuser -u semaphore -- env "ANSIBLE_ROLES_PATH=$CODE_DIR/roles" \
    /opt/ansible/venv/bin/ansible-playbook \
    -i "$INV" playbooks/plan-pve-media-vm.yml \
    -e "media_vm_action=$VM_ACTION" \
    -e "media_raw_disk_action=$DISK_ACTION"
) 2>&1 | tee "$LOG"
```

**Важно:** `set -o pipefail` сохраняет ошибку Ansible при `tee`. Не добавлять `--ask-pass` или пароли в команду/логи. PVE secrets и приватный Fleet Vault остаются за установленной границей доступа. Не запускать `--check` как замену аппаратному preflight.

## Контракт реализации и диагностика

- `playbooks/plan-pve-media-vm.yml`: одна и та же точка входа UI и прямого запуска; два survey-значения строго обязательны.
- `roles/site_inventory_prepare`: получение только site-scoped `media_vm.yml`; актуальные данные на runner не следует предполагать без readback.
- `/var/lib/semaphore/dh-pve-access-audit.request`: nonce-scoped запрос; worker `dh-pve-access-audit.path` — привилегированная граница.
- `/opt/digitalhouses/bootstrap/pve-access/.media-worker-release.json`: SHA-pinned набор исполняемого кода; разные версии checkout и worker блокируют применение. При смене Git commit требуется отдельная проверенная поставка worker, а не отключение SHA-проверки.
- `/run/dh-pve-access-audit.status`: `RUNNING:<nonce>`, `PASS:<nonce>`, `FAILED:<nonce>`; **никогда** не принимать статус чужого nonce.
- `/var/log/dh-pve-access-audit.log`: привилегированный журнал; не публиковать целиком, если могут присутствовать credentials/частные данные.
- `playbooks/media-acceptance.yml`: самостоятельная задача №81, проверяет фактическое состояние; `PASS` только после прохождения обязательных проверок.

## Частные ошибки, зафиксированные в E2E

- `ENABLED_BACKUP_TARGET_OFFLINE`: удалённая media VM предоставляла PVE NFS backup storage. Устранять точечным планом обслуживания и не игнорировать наличие активного `vzdump`. Временно отключённый job **обязательно восстановить после NFS acceptance**.
- `MEDIA_PLEX_PREFERENCES_FORMAT_DRIFT`: реальный корневой элемент Plex `Preferences.xml` называется `Preferences`, не `PlexMediaServer`. Исправлять исходный код и тесты в Git; для уже созданной VM выбирать `keep + import`, а не заново стирать диск.
- После `erase` новый ZFS-пул имеет **новый GUID**. Перед следующим `keep + import` обновить pinned `import_pool_guid` в private Fleet и проверить его совпадение с live `zpool get guid`.

## Запреты для будущих агентов

**Не просить пользователя повторно нажимать Run только потому, что один вызов инструмента не удался.** Сначала восстановить картину по журналам и безопасным штатным механизмам, включая исторический прямой запуск. Если конкретная попытка исполнить Ansible заблокирована платформой, **не обходить** этот отказ: честно показать границу и предложить доступный вариант через интерфейс. Не выполнять destructive-повтор, когда ошибка относится лишь к промежуточной настройке приложения.
