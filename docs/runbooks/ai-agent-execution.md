# AI Agent Operations — единый порядок запуска DigitalHouses Ansible / Semaphore

**Назначение:** обязательный операционный документ для AI-агентов и инженеров. При просьбе «запусти задачу», «примени исправление», «прогони E2E», «обнови сервер», «почему не запускаешь сам?» начинать **здесь**, а не искать старые диалоги и не придумывать новый способ управления инфраструктурой.

**Приоритет и границы:** [AGENTS.md](../../AGENTS.md), [инженерные принципы](../engineering-principles.md), [стандарт Semaphore](../standards/semaphore-task-design.md), разрешения владельца, Fleet и ограничения подключённого инструмента имеют силу. Данный документ **не выдаёт нового доступа**, не отменяет approvals, не является универсальным разрешением на production. Детали медиасервера и диагностики приложений — в [media-semaphore-launch.md](media-semaphore-launch.md).

## 0. Короткая памятка на 30 секунд

1. Проверь доступные подключения: в пилоте рабочий путь — **Remote Desktop Commander → `ansible-serzhan-home` (site runner, CT1231)**. На runner установлен `semaphore`, Ansible и защищённый root-worker.
2. Прочитай текущие `AGENTS.md` и **этот** документ. Уточни site, требуемую задачу/действия, разрешение владельца; не переносить параметры из чужого дома.
3. До любого apply проверь **живую** идентичность site/VM/диска, параллельные процессы, pending worker request, Git SHA checkout, Git SHA pinned worker, целостность manifest и актуальные approvals Fleet.
4. **Основной путь** — штатный Semaphore UI/API, если он доступен и разрешён. **Подтверждённый инженерный путь** — непосредственный запуск **того же Ansible playbook от пользователя `semaphore`** на site runner с тем же site inventory; для этого **не нужен Semaphore API** и не нужен ручной запуск PVE helper.
5. При новой версии кода media worker сперва опубликуй **проверенный pinned release** (см. §3), сверь точное совпадение с checkout. Сам по себе `git pull` или успешные тесты **не** обновляют worker.
6. Запусти с разрешёнными параметрами, запиши журнал и nonce; затем independently verify / Acceptance. Для исправлений программ в уже работающей media VM обычно `keep + import`; **никакого автоматического `recreate` / `erase`**.
7. При **явном security-deny подключённого инструмента** — **STOP для заблокированной операции**. Не повторяй тот же доступ через shell-обёртку, другой connector, API, ключ или повышенные права. Сообщи точную границу и безопасный штатный вариант в UI. Обычные ошибки Ansible/путей/SSH диагностируй в рамках разрешённого канала — не путай их с security-deny.

**Правило коммуникации:** «Semaphore API недоступен» ≠ «запустить Ansible самостоятельно невозможно». «Когда-то был `DEPLOYED`» ≠ «новая версия применена». Не просить пользователя повторно запускать то, что агент может корректно выполнить разрешённым способом.

## Стандартный способ запуска **любой** задачи из каталога

**Один общий механизм для задач 00, 10, 20, 30, 40, 50, 60, 70, 80, 81, 90 и будущих задач** — root-owned pinned CLI `scripts/dh-task-runner.py` из существующего защищённого release. Никаких специальных launch-скриптов для каждого номера или отдельных привилегий для №81. Источник задач — неизменный `catalog/semaphore-tasks.json` **того же Git SHA**, что и checkout.

После разрешённой публикации соответствующего release Bootstrap кладёт launcher в `/opt/digitalhouses/bootstrap/pve-access/scripts/dh-task-runner.py` (root-owned), и включает его SHA256 в manifest. Проверить версию перед выполнением. Старая инсталляция с **69 assets** этого launcher ещё не содержит: до обновления полноценного staged release он **не существует** на runner. Изменён состав assets — нельзя перепубликовать новый release старым циклом «перебрать 69 файлов», требуется staging **нового manifest по обновлённой функции** `stage_pve_access_audit` и его проверка.

Использование (команды выполняются **только** в уже разрешённой root-сессии доверенного site runner; нет автоматического открытия доступа):

```bash
# Заполнить полный проверенный SHA актуального checkout/release:
SHA="<full-approved-40-character-git-sha>"
DIR="/var/lib/semaphore/dh-task-<verified-short-sha>"
TOOL="/opt/digitalhouses/bootstrap/pve-access/scripts/dh-task-runner.py"

# Предварительный план любой конкретной задачи (без запуска playbook):
python3 "$TOOL" --site serzhan-home --task 81 --commit "$SHA" --checkout "$DIR"

# Запуск №81 — без survey (независимая read-only приёмка):
python3 "$TOOL" --site serzhan-home --task 81 --commit "$SHA" \
  --checkout "$DIR" --execute

# Запуск №80 — только с утверждёнными из каталога enum параметрами:
python3 "$TOOL" --site serzhan-home --task 80 --commit "$SHA" \
  --checkout "$DIR" --survey media_vm_action=keep \
  --survey media_raw_disk_action=import --execute
```

**Результат:** launcher сам разрешает номер задачи в playbook, валидирует обязательные survey, site/runner hostname, проверяет SHA чистого checkout, root-owned inventory, корректность доступа к fixed worker (для playbooks, использующих его), отсутствие активного Ansible и pending worker request, последовательность задач, запускает только `runuser -u semaphore -- env -i ... ansible-playbook` и пишет root-only журнал. В коде нет возможности передать свой shell, playbook вне каталога, произвольные extra vars, root SSH/PVE credentials или сменить исполнителя. По умолчанию — **PLAN_ONLY**. `--execute` требуется отдельно; семантическое разрешение владельца и Fleet остаётся обязательным.

**Важно:** этот способ **не даёт AI новых прав**. Если подключённый инструмент явно отклонил выполнение операции (например, прежний запуск №81), нельзя повторить тот же запрещённый запуск с этим launcher под видом «другого метода». Тогда запуск выполняет разрешённый оператор через Semaphore UI либо отдельно утверждённый доступ. Реализация механизма в Git и его установка на site runner — два разных состояния.

**Исторические shell-команды §4/§5** остаются справкой для диагностики старых запусков; для новых AI-чата и новой задачи стандартный путь — **только этот общий launcher**. Прямой путь не создаёт Task/Success в UI Semaphore — результат отмечается отдельно.

## 1. Архитектура и источники истины

| Компонент | Где / зачем |
| --- | --- |
| `DigitalHouses/dh_ansible` | PUBLIC: roles, playbooks, каталог шаблонов, тесты; pin на **полный** Git commit |
| `DigitalHouses/dh_ansible_fleet_detail` | PRIVATE: per-site inventory/approvals/Vault; не копировать секреты в chat/Git/public CI |
| Site runner | Пилот `ansible-serzhan-home`; локальный пользователь `semaphore` без общих root-прав |
| Site inventory (материализация) | `/var/lib/ansible/site-data/<site>/hosts.yml`, `group_vars/all.yml`, `media_vm.yml`, `pve.yml`; это не канонический Fleet |
| Рабочий Ansible | `/opt/ansible/venv/bin/ansible-playbook`, roles path из текущего checkout |
| Direct checkout | `/var/lib/semaphore/dh-media-direct-<short SHA>`; владелец `semaphore`; точный SHA проверяется |
| Protected media worker | `/opt/digitalhouses/bootstrap/pve-access/` + root-only fixed handlers, `dh-pve-access-audit.path` |
| Worker release manifest | `/opt/digitalhouses/bootstrap/pve-access/.media-worker-release.json` (root, 0600), `schema`, `commit`, `assets` с SHA256 |
| Worker verifier | `/usr/local/sbin/dh-pve-media-release-verify --expected-commit <40-char-sha>` |
| Worker request/result | `/var/lib/semaphore/dh-pve-access-audit.request`, `/run/dh-pve-access-audit.status`; связка nonce + status |
| Semaphore task catalog | `catalog/semaphore-tasks.json`; обновление каталога ≠ обновление protected worker ≠ обновление приватного Fleet |

**Разделение привилегий:** `semaphore` запускает только обычный playbook, формирует разрешённый запрос, но не получает root SSH credentials. Привилегированные шаги PVE/guest делает *уже установленный* fixed-purpose root-worker, сверяющий site, nonce, Git commit и hashes. Нельзя вызывать `dh-pve-media-deploy` напрямую или обходить worker записью в SQLite/ручной подделкой request/status.

## 2. Обязательный preflight (перед тем как сказать «нет доступа» или выполнить write)

Вначале **read-only**, на авторизованном runner, если подключённый инструмент разрешает:

```bash
hostname -s
id semaphore
date -Is
test -r /var/lib/ansible/site-data/serzhan-home/hosts.yml
systemctl is-active dh-pve-access-audit.path
ps -eo pid,etime,args | grep -E '[a]nsible-playbook|[d]h-pve-media-deploy'
test ! -e /var/lib/semaphore/dh-pve-access-audit.request && echo WORKER_REQUEST_IDLE
python3 - <<'PY'
import json
p = '/opt/digitalhouses/bootstrap/pve-access/.media-worker-release.json'
data = json.load(open(p))
print('WORKER_RELEASE_COMMIT=' + data['commit'])
print('WORKER_ASSETS=' + str(len(data['assets'])))
PY
```

Это **пример для пилотного site**, не разрешение обходить права и не полный аппаратный preflight. До apply дополнительно необходимо: проверить текущие VMID/status, физический WWN и GUID ZFS, сетевую идентичность, `pve.yml`, `media_vm.yml`, backup/NFS-зависимости, активные задания PVE и approvals. Если требуемая проверка недоступна или результат неоднозначен, `BLOCKED`; не угадывать по сохранённому значению. Site-specific данные должны поступать из проверенного Fleet.

**Проверка целостности до изменения:**

```bash
# EXPECTED_SHA — 40-символьный commit из фактически утверждённого pinned release
python3 /usr/local/sbin/dh-pve-media-release-verify --expected-commit "$EXPECTED_SHA"
```

Ожидается `MEDIA_WORKER_RELEASE=PASS FILES=N`. N — фактическое число манифеста, **не фиксировать 69 как универсальное**.

## 3. Как публиковать новую версию protected worker

**Это отдельная операция**. После Git-коммита, локальных тестов или изменения Ansible roles worker остаётся на старом SHA, пока assets не доставлены и manifest не обновлён.

**Поддерживаемая реализация первичного/штатного staging:** функция `stage_pve_access_audit(checkout, expected_commit)` в [`bootstrap/semaphore-host-install.py`](../../bootstrap/semaphore-host-install.py). Она доставляет pin-список roles/playbooks/scripts на доверенный runner, записывает manifest **последним** и проверяет `MEDIA_WORKER_RELEASE=PASS`. Полный Bootstrap/режим `--resume-semaphore` — не универсальная команда «обновить одну media роль»: он затрагивает также commissioning/catalog, поэтому не запускай его без соответствующего задания и проверок.

**Подтверждённая инженерная операция на уже установленном пилотном runner (30.10.2026):** отдельная публикация root-owned worker assets **без полного Bootstrap**, затем проверка SHA. Применялась при переходах `f0694af` → `4e3d4c` → `653a0fc`. Исторический порядок:

1. Checkout точной **новой** ревизии в root-only staging; проверить полный commit, отсутствие локальных изменений и необходимые регрессионные тесты; убедиться, что список публикуемых assets согласован с версией кода.
2. Проверить предыдущий release целиком, отсутствие параллельного `ansible-playbook`, текущего root-worker service и pending request. Остановить `dh-pve-access-audit.path` **только в согласованном maintenance window**.
3. Сделать уникальный root-only backup **всего** `/opt/digitalhouses/bootstrap/pve-access` и двух внешних fixed handlers. Не удалять предыдущую копию.
4. Опубликовать **только** asset paths, зарегистрированные версионированным staging contract, с правильными владельцем/правами и SHA. Для каждого файла — временный файл в том же каталоге, затем атомарный `os.replace`. Не копировать целиком Git checkout поверх worker. Если меняется состав assets, не переиспользовать старый список: использовать контракт новой ревизии и отдельно проверять каждый путь/права.
5. Последним атомарно опубликовать manifest `{schema:1, commit:SHA, assets:{relative_path:SHA256}}` в режиме 0600. Запустить штатный `dh-pve-media-release-verify --expected-commit SHA`, удостовериться `PASS`, восстановить active watcher и отсутствие pending request.
6. При любой ошибке публикации **не запускать deploy**; зафиксировать отказ, восстановить согласованный release из root-only backup по утверждённой процедуре и повторно проверить целостность. Не отключать verifier, не ставить ложный SHA. Перед новым запуском убедиться в согласованности checkout и worker.

Это **проверенная последовательность из журналов**, а не предоставление AI-агенту права на произвольную замену root-owned файлов. Если у текущего подключения или политики нет разрешения выполнить публикацию, остановиться и предложить штатный операторский запуск. Не придумывать альтернативный self-update worker.

### Рабочий пример точечного staging на существующем runner

Это **операторский reference** исторической операции от 30.10.2026 (без вызова полного Bootstrap), **не автоматическое разрешение**. Исполняется только из уже авторизованной root-сессии site runner, после §2, явного approval на замену worker и проверки **неизменности состава staged assets** между старой и новой ревизиями. Если функция `stage_pve_access_audit()` добавляла/удаляла пути, **не использовать** старую asset map: остановиться и выполнить новое согласованное staging.

Значения `NEW_SHA`, `OLD_SHA`, `CODE_DIR` оператор подставляет из фактического Git/Fleet/manifest, не из истории. Ниже нет ни SSH-команд к PVE, ни секретов; target — **только** внутренний pinned worker, не media VM.

```bash
# Проверки и неизменяемые входы перед root-публикацией.
NEW_SHA="<verified-full-40-char-sha>"
OLD_SHA="<current-manifest-40-char-sha>"
CODE_DIR="/root/<verified-clean-staging-checkout>"
test "$(git -C "$CODE_DIR" rev-parse HEAD)" = "$NEW_SHA"
test -z "$(git -C "$CODE_DIR" status --porcelain)"
test -z "$(git -C "$CODE_DIR" diff "$OLD_SHA" "$NEW_SHA" -- bootstrap/semaphore-host-install.py)"
python3 /usr/local/sbin/dh-pve-media-release-verify --expected-commit "$OLD_SHA"
test ! -e /var/lib/semaphore/dh-pve-access-audit.request
test "$(systemctl is-active dh-pve-access-audit.service || true)" != active
test "$(ps -eo args | grep -Ec '^[^ ]*ansible-playbook .*[p]lan-pve-media-vm.yml' || true)" = 0

# После подтверждения отсутствия параллельных task/worker и approval:
NEW_SHA="$NEW_SHA" OLD_SHA="$OLD_SHA" CODE_DIR="$CODE_DIR" python3 - <<'PY'
import hashlib, json, os, shutil, stat, subprocess, tempfile
from pathlib import Path

new_sha, old_sha = os.environ["NEW_SHA"], os.environ["OLD_SHA"]
src = Path(os.environ["CODE_DIR"])
dst = Path("/opt/digitalhouses/bootstrap/pve-access")
manifest = dst / ".media-worker-release.json"
request = Path("/var/lib/semaphore/dh-pve-access-audit.request")
external = {
    "bootstrap/pve-access-audit-request.sh": Path("/usr/local/sbin/dh-pve-access-audit"),
    "bootstrap/dh-pve-media-release-verify.py": Path("/usr/local/sbin/dh-pve-media-release-verify"),
}
def run(*args):
    subprocess.run(args, check=True, capture_output=True)
assert os.geteuid() == 0
assert subprocess.check_output(["git", "-C", str(src), "rev-parse", "HEAD"],
                               text=True).strip() == new_sha
assert not request.exists()
assert subprocess.run(["systemctl", "is-active", "--quiet",
                       "dh-pve-access-audit.service"]).returncode != 0
before = json.loads(manifest.read_text())
assert before["schema"] == 1 and before["commit"] == old_sha
assert len(before["assets"]) >= 30
# All asset destinations and their parent directories must already be trusted.
for rel in before["assets"]:
    item = Path(rel)
    assert not item.is_absolute() and ".." not in item.parts
    target = external.get(rel, dst / rel)
    assert target.is_file() and not target.is_symlink()
    assert target.stat().st_uid == 0
    assert (src / rel).is_file() and not (src / rel).is_symlink()
backup = Path(tempfile.mkdtemp(prefix="dh-media-worker-before-",
                               dir="/root"))
os.chmod(backup, 0o700)
shutil.copytree(dst, backup / "pve-access")
for rel, target in external.items():
    shutil.copy2(target, backup / Path(rel).name)
print("WORKER_BACKUP=" + str(backup), flush=True)

# A failure before the new manifest is committed leaves an invalid mixed
# release; verifier will fail closed. Do not deploy or conceal the failure.
run("systemctl", "stop", "dh-pve-access-audit.path")
try:
    assert not request.exists()
    assets = {}
    for rel in sorted(before["assets"]):
        raw = (src / rel).read_bytes()
        target = external.get(rel, dst / rel)
        mode = stat.S_IMODE(target.stat().st_mode)
        with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as fh:
            fh.write(raw)
            staged = Path(fh.name)
        os.chown(staged, 0, 0)
        os.chmod(staged, mode)
        os.replace(staged, target)
        assets[rel] = hashlib.sha256(raw).hexdigest()
    with tempfile.NamedTemporaryFile(dir=dst, delete=False) as fh:
        fh.write((json.dumps({"schema": 1, "commit": new_sha,
                              "assets": assets}, sort_keys=True) + "\n").encode())
        stamped = Path(fh.name)
    os.chown(stamped, 0, 0)
    os.chmod(stamped, 0o600)
    os.replace(stamped, manifest)  # Release stamp ALWAYS LAST
    run("python3", "/usr/local/sbin/dh-pve-media-release-verify",
        "--expected-commit", new_sha)
finally:
    run("systemctl", "start", "dh-pve-access-audit.path")
print("WORKER_RELEASE=PASS", new_sha, flush=True)
PY
```

**Предусловие, которое не заменяет этот код:** перечень assets между релизами должен быть одинаковым. Проверку `git diff OLD..NEW -- bootstrap/semaphore-host-install.py` использовать как строгий conservative gate: если файл менялся, необходимо ручное ревью контракта и другой утверждённый staging. Прерванный staging оставляет watcher включённым, но manifest verification обнаружит несогласованные файлы. В этом случае **не запускать** №80; использовать root-only backup и утверждённую процедуру rollback. Никаких «поправим SHA вручную».

**Проверка после staging:**

```bash
# В корне разрешённого site runner после публикации
CODE_DIR="/var/lib/semaphore/dh-media-direct-<verified-short-sha>"
EXPECTED_SHA="<full-40-char-sha>"
test "$(runuser -u semaphore -- git -C "$CODE_DIR" rev-parse HEAD)" = "$EXPECTED_SHA"
python3 /usr/local/sbin/dh-pve-media-release-verify --expected-commit "$EXPECTED_SHA"
systemctl is-active dh-pve-access-audit.path
test ! -e /var/lib/semaphore/dh-pve-access-audit.request
```

**Важно:** несовпадение checkout SHA и pinned worker commit = **`BLOCKED`**, даже если каталоги, HTTP-сервисы и `systemctl` выглядят исправными.

## 4. Как AI-агент запускает задачу без Semaphore API

**Предпочтение:** штатный пользовательский Task в Semaphore через доступный авторизованный UI/API, с существующим шаблоном и его survey-параметрами. **Не** создавать task через запись SQLite или подмену состояния сервера.

**Проверенный fallback:** запуск *того же* playbook напрямую на site runner **от `semaphore`**, с его обычным inventory и с уже опубликованным идентичным pinned worker release. Пример **только для подтверждённого пилота** и **только после preflight и разрешения операции**:

```bash
set -euo pipefail
SITE_ID="serzhan-home"
CODE_DIR="/var/lib/semaphore/dh-media-direct-<verified-short-sha>"
EXPECTED_SHA="<full-40-char-sha>"
VM_ACTION="keep"
DISK_ACTION="import"
INV="/var/lib/ansible/site-data/$SITE_ID/hosts.yml"
LOG="/var/log/dh-media-direct-$(date -u +%Y%m%dT%H%M%SZ).log"

test -f "$INV"
test -f "$CODE_DIR/playbooks/plan-pve-media-vm.yml"
test "$(runuser -u semaphore -- git -C "$CODE_DIR" rev-parse HEAD)" = "$EXPECTED_SHA"
python3 /usr/local/sbin/dh-pve-media-release-verify --expected-commit "$EXPECTED_SHA"
test ! -e /var/lib/semaphore/dh-pve-access-audit.request

(
  cd "$CODE_DIR"
  runuser -u semaphore -- env "ANSIBLE_ROLES_PATH=$CODE_DIR/roles" \
    /opt/ansible/venv/bin/ansible-playbook \
    -i "$INV" playbooks/plan-pve-media-vm.yml \
    -e "media_vm_action=$VM_ACTION" \
    -e "media_raw_disk_action=$DISK_ACTION"
) 2>&1 | tee "$LOG"
echo "RUNNER_LOG=$LOG"
echo "TERMINAL STAYS OPEN"
```

Текст `<verified-short-sha>` / `<full-40-char-sha>` — **заполнить после реальной проверки**, не вставлять буквальные скобки. `VM_ACTION` и `DISK_ACTION` — **два независимых параметра**, оба берутся из явного разрешения и Fleet. Для восстановления приложений без удаления VM/ZFS — `keep + import`. `create`, `recreate`, `erase` — только при конкретно утверждённых условиях. Даже при `keep + import` проверить возможные побочные операции в текущей версии роли.

**Что даёт direct запуск:** исполняет тот же audit → plan → approved apply → verify; сохраняет logs и nonce; **не** создаёт UI Task №NN в Semaphore. Нельзя называть его «Task #NN Success» без реальной записи Semaphore. Если при этом root-worker возвращает `FAILED` / `BLOCKED` — прочитать безопасный журнал и исправить причину в Ansible, **не** запускать privileged helper вручную.

## 5. После apply: независимый acceptance и отчёт

- Проверить фактическую VM (ресурсы/boot/guest-agent), ZFS GUID и здоровье, SMB/NFS, доступность приложений и их реальный функционал. Отдельно проверить зависимости, которые task могла временно отключить.
- **Media Acceptance №81** — отдельный `playbooks/media-acceptance.yml` / штатный шаблон Semaphore №81 и его защищённый read-only worker. Он не заменяет сценарий просмотра фильма, рабочую форму входа Filestash и реальную загрузку постеров; при `SKIP` явно перечислить непротестированные случаи.
- Минимальный отчёт: `site`, `Git SHA checkout`, `Git SHA worker`, `mode`, `disk action`, `способ запуска`, `журнал`, `nonce`, `result worker`, `play recap`, `независимые проверки`, `remaining BLOCKED/SKIP`.
- Не считать `EXIT_STATUS=0`, `PLAY RECAP failed=0`, `HTTP 200`, `DEPLOYED` или старую запись Semaphore достаточным доказательством полной приёмки. При неизменённом состоянии проверить idempotency повторным разрешённым apply (`changed=0` для идемпотентных этапов).
- Результаты различать: `AUDIT_PASS`, `PLAN_ONLY`, `APPLY_CHANGED`, `APPLY_UNCHANGED`, `VERIFY_PASS`, `BLOCKED`, `UNKNOWN`.

**Проверенный запуск независимого read-only Acceptance №81** при уже проверенных `SITE_ID`, `CODE_DIR`, `EXPECTED_SHA` и `INV` из §4 (тот же pinned worker, без смены прав и без PVE SSH у `semaphore`):

```bash
set -euo pipefail
test "$(runuser -u semaphore -- git -C "$CODE_DIR" rev-parse HEAD)" = "$EXPECTED_SHA"
python3 /usr/local/sbin/dh-pve-media-release-verify --expected-commit "$EXPECTED_SHA"
LOG81="/var/log/dh-media81-direct-$(date -u +%Y%m%dT%H%M%SZ).log"
(
  cd "$CODE_DIR"
  runuser -u semaphore -- env "ANSIBLE_ROLES_PATH=$CODE_DIR/roles" \
    /opt/ansible/venv/bin/ansible-playbook \
    -i "$INV" playbooks/media-acceptance.yml
) 2>&1 | tee "$LOG81"
echo "MEDIA81_LOG=$LOG81"
```

Этот пример допустим только при подтверждённом отсутствии параллельного worker request и разрешённом доступе. Вывод `MEDIA81_RESULT=PASS CRITICAL_FAILURES=0` означает успешность **определённых acceptance checks**, а не подтверждает неохваченные ими сценарии; `FAIL` и `SKIP` разбирать отдельно.

## 6. Карта отказов — действия без потери времени

| Симптом | Что означает | Действие |
| --- | --- | --- |
| Нет Semaphore API / нет Task ID | UI/API launch не выполнен; runner всё ещё может быть доступен | Проверить §2, затем **разрешённый** direct путь §4 |
| Нет root SSH у `semaphore` | Ожидаемое разграничение привилегий | Не выдавать ключ/не использовать `sudo`; применять fixed worker |
| Git SHA checkout ≠ worker commit | Нельзя выполнять новую версию | Проверенный staging §3 → verifier → запуск |
| Worker request pending / другой Ansible | Риск параллельного write | STOP, read-only выяснить состояние; не сбрасывать nonce |
| PVE/VM/ZFS не в утверждённом live-state | Неясный target или риск потери данных | STOP, audit/Fleet/recovery, не менять на `erase` |
| Обычная ошибка Ansible задачи | Действительно произошёл отказ playbook | Разобрать лог, исправить код, прогнать тесты и повторить тот же разрешённый путь |
| Удалённый инструмент сообщает **security-deny** | Конкретное действие запрещено на уровне подключения | **STOP; не обходить другим каналом**. Назвать заблокированный вызов и предложить UI-операцию |
| Старый run `DEPLOYED` | Исторический результат, не текущий | Проверять новую ревизию и live-state заново |
| Filestash HTTP 200, вход сломан | Проверка порта не равна проверке IDP | Проверка формы login в task №80/81, см. [media runbook](media-semaphore-launch.md) |
| Prowlarr `Startup folder not writable` | Свойства каталога установки препятствуют updater | Проверка per-app ownership; использовать новое закреплённое обновление media роли |

## 7. Исторически подтверждённые доказательства (справка, НЕ инструкция копировать старые значения)

- **28.10.2026:** прямой запуск `runuser -u semaphore` для `playbooks/plan-pve-media-vm.yml`. Журнал `/var/log/dh-media-direct-4a39285-20261028.log`: `PLAY RECAP ... failed=0`, `result: DEPLOYED`.
- **30.10.2026:** подтверждена отдельная публикация worker release с backup → stop watcher → atomic assets → manifest last → `MEDIA_WORKER_RELEASE=PASS FILES=69` → watcher active. Ревизии `f0694af8a0c49d5d55b45e8cca0582ced8e4d360`, затем `4e3d4c1e136c96320b78d9ccb5cdddd59281d06c`, затем `653a0fc5afaf4dc60c2c09828b8f4ad2b246face`.
- **30.10.2026:** прямой запуск с `runuser -u semaphore` использовал `ANSIBLE_ROLES_PATH=/var/lib/semaphore/dh-media-direct-f0694af/roles`, site inventory и `playbooks/plan-pve-media-vm.yml`. Исторический режим `recreate + import` **не переносить** на исправление приложений; сегодня для него требуется отдельное согласование.
- Различать `MEDIA_WORKER_RELEASE=PASS` (прошла проверка pin-целостности), `WORKER_UPDATED=PASS` (отчёт конкретного процесса), `EXIT_STATUS=0` (команда завершилась), `DEPLOYED` (этап apply сообщил успех) и **действительный независимый E2E**.

## 8. Как поддерживать этот документ

- **Единственный нормативный источник для AI о порядке исполнения/публикации/проверке** — именно этот документ. `AGENTS.md`, `README.md` и стандарт Semaphore содержат короткую ссылку **сюда**, а специализированные runbook только особенности задач.
- Когда найден новый работающий механизм: после live-подтверждения обнови **этот документ**, укажи пред- и постусловия, где применимо, что запрещено и чем подтверждено. Не оставлять решение только в истории чата.
- Если код release-стейджинга меняется, сверить этот документ с `bootstrap/semaphore-host-install.py`, `bootstrap/dh-pve-media-release-verify.py`, `bootstrap/pve-access-audit-request.sh`, стандартом и тестами; не выдавать историческую ручную процедуру за универсально внедрённый автоматический publisher.
- Изменения документации в Git **не перезаписывают инструкции проекта ChatGPT**. Если модель нового чата не видит Git автоматически, единственная ссылка из инструкций проекта должна вести к `AGENTS.md` или этому файлу. 
