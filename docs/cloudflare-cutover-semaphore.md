# Semaphore №10 — Cloudflare Tunnel до Proxmox VE

**Дата:** 02.11.2026. **Статус:** Task #32 LIVE PASS, idempotent rerun (`changed=0`), Cloudflare Access challenge independently verified; authenticated browser login remains required.

## Почему изменили схему

Live Task #27 обнаружила существующий общий Tunnel `<site.id>` с четырьмя ingress-правилами: PVE-hostname совпадал, но origin и TLS-параметры отличались от нового PVE. Прежняя задача блокировала запись с `CF_EXISTING_INGRESS_NOT_OWNED` и `changed=0`. Этот общий Tunnel может обслуживать другие приложения; его ingress и коннекторы **не менять**.

Новая модель: **один отдельный Tunnel для PVE**, `<site.id>-pve`. Старый общий `<site.id>` сохраняется с исходными четырьмя правилами. Публичное имя PVE не меняется: `dh-<site.id>-pve.digitalhouses.vip`.

## Алгоритм №10

1. **Read-only preflight:** site/runner identity, pinned SSH/PVE CA, актуальный PVE IP, Cloudflare zone, единственная покрывающая Access application и ограниченные allow-policy, старый CNAME и точный site Tunnel. Если CNAME указывает на старый общий Tunnel, через API подтверждаем, что ровно одно его ingress-правило соответствует нашему PVE-hostname. Не выводим чужие DNS/ingress/токены и не перезаписываем их.
2. **PVE package/service preflight:** проверка фактического PVE node, наличия и принадлежности `cloudflared.service`; при отсутствии `cloudflared` установить через подписанный официальный APT; проверить, что бинарник поддерживает `--token-file` (версия >= 2025.4.0). Чужой systemd unit не заменять. Прежде чем создавать Tunnel, убедиться в готовности ПО. На этом этапе пустую службу запускать **нельзя** — ещё нет token.
3. **Tunnel provisioning:** найти или создать отдельно `<site.id>-pve`, установить только один PVE ingress `https://<observed-PVE-IP>:8006` с `caPool=/etc/cloudflared/dh-pve-ca.pem`, `noTLSVerify=false` и конечный `http_status:404`. Чужие правила в новом туннеле блокируют изменения. Получить connector token через Cloudflare API, скрыть его из логов.
4. **systemd activation:** положить токен в root-only файл на PVE, установить own systemd unit, включить и поднять `cloudflared.service`; повторный запуск без изменения токена и unit не делает restart. `systemctl active` обязателен.
5. **Только после запуска:** Cloudflare API подтверждает `Tunnel status=healthy` (не `inactive/down/degraded`); происхождение PVE проверяется отдельно HTTPS с доверенным PVE CA. `Healthy` нельзя требовать **до создания туннеля** — Cloudflare выдаёт статус только после подключения connector.
6. **DNS cutover:** только после Access, Healthy, strict origin TLS и повторной проверки DNS. Если CNAME отсутствует — создать. Если указывает на свой dedicated Tunnel — ничего не менять. Если указывает на прежний `<site.id>`, который ранее точно подтверждён как прежний владелец PVE-hostname — заменить **только этот CNAME**. Не менять старый Tunnel, его ingress и два других маршрута. Если DNS изменился во время задачи — блокировка `CF_DNS_CONCURRENT_CHANGE_OR_FOREIGN_OWNER`.
7. **Acceptance:** readback CNAME, HTTPS redirect на Cloudflare Access для неаутентифицированного клиента, затем отдельная проверка авторизованного входа в PVE. Повтор №10 должен завершаться без инфраструктурных изменений.

## Безопасность и credentials

Секреты из native Semaphore №05/06, root SSH с закреплённым host key, фактический PVE из bootstrap. Не требуются новые Survey/ручные root scripts, `dh-cloudflare-switch.path` или повторный ввод пароля. API/токены скрыты `no_log`; токен на PVE только root:root `0600`; не передаётся как аргумент shell.

**Важное отличие:** `systemctl active` доказывает работу локального процесса, а `healthy` от Cloudflare подтверждает подключения к edge. Ни одно из них само по себе не доказывает успешный вход в PVE через Access.

## LIVE Task #31 · 02.11.2026 — финальная проверка Proxmox API

Подтверждены: установка `cloudflared 2026.10.7` на PVE, запуск
`cloudflared.service` (`active/enabled`), отдельный
`<site.id>-pve` Tunnel в состоянии **Healthy** и readback ожидаемого
ingress c `caPool` и строгим TLS. Существующий общий site Tunnel
не изменялся. На этапе read-only origin check PVE ответил
`401 No ticket` по адресу `/api2/json/version`; это нормальная защита
этого API endpoint, а не ошибка сертификата или `cloudflared`.

Отдельная диагностика с pinned PVE CA подтвердила:
`/` → HTTP 200, `/api2/json/version` → HTTP 401,
`/api2/json/access/domains` → HTTP 200 с JSON `data` (список);
`ssl_verify_result=0` во всех случаях.

Исправление: для проверки Proxmox API до публикации DNS теперь
используется не требующий ticket read-only
`/api2/json/access/domains`. Строгая проверка TLS, `ca_path`,
HTTP 200, структура JSON и заголовок `pve-api-daemon` обязательны.
Не подставлять PVE root password, ticket или `validate_certs: false`.

Для Task #31 DNS cutover и Access challenge ещё не подтверждались; итоговая приёмка выполнена в Task #32 ниже.

## LIVE Task #32 · 02.11.2026 — PASS и idempotency

С использованием Git commit `cc789d3` Semaphore №10 отработала успешно,
`runner ok=52 changed=0 failed=0`,
`cf_pve_target ok=17 changed=0 failed=0`.
Повторный прогон не менял ни API Cloudflare, ни конфигурацию PVE:

- существующий отдельный Tunnel `serzhan-home-pve` найден и подтверждён
  `HEALTHY`; создание Tunnel и запись ingress пропущены как ненужные;
- TLS origin `<observed-PVE-IP>:8006`, PVE API и строгий CA readback — PASS;
- пакет и systemd unit `cloudflared` уже установлен/active, restart не потребовался;
- DNS PVE CNAME найден правильным; Access policy/redirect — VERIFIED;
- `shared_tunnel_untouched=true`, `legacy_deleted=false`, старый общий
  Tunnel `serzhan-home` оставлен без изменений.

Независимая read-only проверка с подключённого site runner:
`https://dh-serzhan-home-pve.digitalhouses.vip/` ответил **HTTP 302** на
Cloudflare Access `/cdn-cgi/access/login/`. URL авторизации с временными
параметрами не сохранять в Git и отчётах.

**Открытый пункт:** успешный браузерный вход *после* Access с подтверждением
PVE UI ещё не засвидетельствован. Инфраструктурная часть №10 — LIVE PASS;
полный пользовательский E2E допускает отдельную проверку.

## Выполнение и приёмка

После проверки PR/merge — один запуск **`10-Создание туннеля CF до PVE`** в Semaphore. При ошибке прислать полный task log (без секретов). Не удалять общий Tunnel и не чистить его маршруты. После PASS открыть адрес в браузере, авторизоваться в Access и убедиться, что действительно показана новая PVE на фактическом IP.

Профильный тест CI и `ansible-playbook --syntax-check` обязательны. Live состояние не объявляется успешным до результатов реального запуска и проверки браузером.

Официальные источники:
- https://developers.cloudflare.com/tunnel/get-started/
- https://developers.cloudflare.com/tunnel/observability/
- https://developers.cloudflare.com/tunnel/reference/tunnel-tokens/
- https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/configure-tunnels/run-parameters/
