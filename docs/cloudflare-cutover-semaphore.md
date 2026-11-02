# Semaphore №10 — Cloudflare Tunnel до Proxmox VE

**Дата:** 02.11.2026. **Статус:** новая реализация через Ansible в PR; до живого запуска нельзя объявлять LIVE PASS.

## Что меняется

Задача **10-Создание туннеля CF до PVE** выполняет в одном запуске
`audit → preflight → reconcile → verify`. Устанавливать старый
`dh-cloudflare-switch.path`, отдельный root worker или запускать ручной
`cloudflare-commission.py --enroll` на свежем объекте **не требуется**.

Один доверенный контур Semaphore/Ansible использует уже полученные:
- Cloudflare API credential `DH_CLOUDFLARE_API_TOKEN` из native Semaphore Variable Group (Task 05);
- root SSH-ключ PVE и pinned known_hosts, проверенные Task 06;
- фактическую PVE identity из bootstrap `pve-runtime.yml`, CA `pve-root-ca.pem`, локальную `site.id`.

Никаких новых Survey-полей и повторного ввода credentials. На
`serzhan-home` текущий PVE — **<observed-PVE-IP>**, не старый адрес Fleet.
Дом определяется site identity, а не жёстко заданным IP в общем коде.

## Границы безопасности

Публичное имя: `dh-<site.id>-pve.digitalhouses.vip`.
Туннель: `<site.id>` (`config_src=cloudflare`).
Сервис на origin: `https://<observed-pve-ip>:8006`.

До любого Cloudflare POST/PUT задача требует:
1. точное совпадение site/runner/PVE, работающий root SSH с проверкой host key;
2. Cloudflare Zone Read, однозначную активную зону `digitalhouses.vip`;
3. **ровно одно** действующее приложение Access, покрывающее точное имя
   либо утверждённый wildcard `*.digitalhouses.vip`;
4. действующие ограниченные allow-политики с email, без bypass/everyone;
5. отсутствие чужого туннеля, DNS-конфликта или неразрешённого ingress drift.

Отсутствие Access **не приводит к публичному открытию PVE** — задача блокируется
с `CF_ACCESS_APPLICATION_MISSING_OR_AMBIGUOUS` или
`CF_ACCESS_ALLOWLIST_NOT_PROVEN`. Этот выпуск не придумывает допустимые
email/группы и не меняет общие приложения Access других объектов.

При отсутствии site-туннеля создаётся remote-managed Tunnel через Cloudflare
API, выставляется **только** canonical ingress + 404 fallback. При
существующем site-туннеле выполняется readback/adopt; чужие маршруты
не перезаписываются. Из Cloudflare API берётся connector token и Ansible
записывает его на PVE с root:root 0600, **не в CLI argv**, не в Git.
Origin использует PVE CA и `originServerName`, `noTLSVerify=false`.

На PVE Ansible устанавливает `cloudflared` из подписанного официального
APT-репозитория только при отсутствии пакета; установку и `systemd`
выполняет через обычное root SSH. Если существующая служба
`cloudflared.service` принадлежит legacy/неизвестному развёртыванию, задача
**BLOCKED**, не прерывает её и не пытается автоматически перенести старый
туннель. Никаких скрытых перезапусков чужих служб. При изменении токена, CA
или service unit только тогда выполняется restart; повтор совпадающего
состояния — read-only + штатная проверка службы.

DNS CNAME создаётся **только после подтверждения Cloudflare API
`healthy`** и повторного чтения ingress. После публикации проверяется
точный DNS и неаутентифицированный HTTP redirect на Cloudflare Access.
**Browser login с правами пользователя остаётся отдельной проверкой**
и не подменяется успешным API ответом.

## Как запустить после merge

1. В Semaphore обновить **№00-Обновить список задач**, если требуется
   получить новый Git SHA каталога (шаблон №10 остаётся с прежним ID).
   Никакого Bootstrap/удаления CT1231/повторной №05/№07.
2. Запустить **№10-Создание туннеля CF до PVE** без Survey-параметров.
3. Сохранить полный безопасный Task output и сообщить ошибку
   `CF_...`, если есть. Сообщения облачного API и токены не выводятся
   (`no_log`).
4. После PASS открыть `https://dh-serzhan-home-pve.digitalhouses.vip`,
   пройти Access и проверить PVE UI, потом **повторить №10**.
   У повторного запуска инфраструктурные изменения должны быть 0.

**Важно:** установка/доступность пакета, Cloudflare permission WRITE и
реальная внешняя авторизация **ещё не доказаны** до живого E2E.
Отказы Cloudflare 403 на POST/PUT или получение tunnel token означают,
что имеющийся credential не имеет нужного права; GET-only диагностика №06
никогда не считалась доказательством возможности записи.

## Контракт для AI-агентов

- `target`: Cloudflare API + PVE host;
- `operations`: external API, software_manage, service_manage;
- `identity`: Semaphore native CF credential + PVE commissioned root SSH;
- `credential_source`: Task 05/06 native Semaphore + bootstrap trust;
- `approval`: запуск №10 владельцем, только exact site, без destructive migration;
- `postconditions`: API tunnel healthy, protected ingress, service active,
  DNS readback, Access challenge, второй запуск без изменений;
- `privilege_status`: PVE root SSH READY по №06, CF WRITE — PENDING до E2E.

Старые инструкции по ручной root-only Cloudflare enrollment и watcher
сохранены в Git **только для legacy-развёртываний**. Они не являются
пререквизитом новой №10, не удаляются автоматически и не используются
для новых объектов.

Официальные источники:
- https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/get-started/create-remote-tunnel-api/
- https://developers.cloudflare.com/api/resources/zero_trust/subresources/tunnels/subresources/cloudflared/
- https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/configure-tunnels/origin-parameters/
- https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/configure-tunnels/run-parameters/
