# ADR-0006 — Pragmatic NUT security model for residential UPS

Статус: **Accepted**. Дата: 2026-10-06.

## Контекст

DigitalHouses в первую очередь автоматизирует домашние и малые локальные установки PVE с USB UPS.

Практический риск в этом классе объектов — не только избыточные права, но и отсутствие работающей UPS automation вообще. Многие пользователи подключают USB-кабель UPS и годами не настраивают NUT, мониторинг, battery tests или автоматическое выключение хоста. Поэтому решение должно быть достаточно простым, чтобы его можно было массово и надёжно разворачивать и сопровождать.

Для локального UPS на PVE NUT server слушает loopback, а DigitalHouses PVE Agent работает на том же хосте. В этом scope отдельные NUT identities для UI controls и FSD признаны неоправданной сложностью.

## Решение

Для PVE с локальным managed UPS используется **один** managed NUT user:

```text
dh_primary_user
```

Ему разрешены:

```text
upsmon primary
instcmds = ALL
```

Пароль:

- генерируется один раз криптографически случайно;
- хранится как managed secret;
- переиспользуется при повторных Ansible runs;
- не ротируется без отдельной причины;
- не выводится в Git, stdout, Ansible logs или operator reports.

Те же credentials могут использоваться DigitalHouses PVE Agent для локального NUT command plane.

## Scope и границы

Это решение применяется к **домашнему/малому локальному UPS**, подключённому к тому же PVE по USB, когда NUT server доступен только локально, обычно на:

```text
127.0.0.1:3493
```

Не требуется:

- отдельный NUT user для battery-test/beeper controls;
- отдельный NUT user для `upsmon primary`;
- отдельные per-command ACL users;
- дополнительный security layer только ради разделения локальных UPS controls и FSD.

Если в будущем NUT будет опубликован в LAN/WAN, появится multi-host topology, удалённые NUT clients или иной существенно более широкий trust boundary, это решение должно быть пересмотрено отдельным ADR.

## Controls не равны shutdown policy (уточнение 2026-10-13)

NUT PRIMARY и нативное событие Low Battery относятся к базовой конфигурации NUT на PVE. Ansimble-2 может подготовить пользователя NUT и включить штатный nut-monitor после проверки состояния UPS.

Ansimble-2 НЕ устанавливает software-пороги заряда/времени, НЕ рассчитывает shutdown budget, НЕ запускает собственный FSD helper и НЕ меняет конфигурацию, данные или службу PVE Agent. Включение NUT PRIMARY не активирует software-пороги агента: ими управляет только PVE Agent и его UI в Home Assistant.

Site-инвентарь для native NUT PRIMARY использует ups.nut.fsd.enabled. Наличие command credentials само по себе не разрешает software-triggered shutdown.

## UI / product boundary

PVE Agent определяет capabilities через NUT и публикует в Home Assistant только поддерживаемые продуктом controls.

Для поддерживаемого ИБП ожидаются battery-test controls и beeper control, если они рекламируются UPS.

Опасные power commands, такие как `load.off`, `load.on`, `shutdown.return`, `shutdown.stayoff`, а также произвольный user-facing `upscmd`, не должны публиковаться в Home Assistant.

Это ограничение является частью product behavior PVE Agent, а не причиной создавать дополнительные NUT identities в Ansimble.

## Operational safety

До изменения NUT configuration выполняется read-only audit существующих:

- selected UPS;
- `/etc/nut/upsd.users`;
- `/etc/nut/upsmon.conf`;
- DigitalHouses PVE Agent configuration;
- systemd state соответствующих NUT units.

Изменения должны быть idempotent, secret-safe и иметь backup/rollback для изменяемых конфигурационных файлов.

Credential acceptance выполняется безопасным probe без battery test, FSD, `INSTCMD` и power commands.

Активация аварийного shutdown — отдельный change с отдельным plan/apply/verify.

## Обоснование

Для residential UPS при локальном loopback-only NUT разделение одного локального control plane на несколько пользователей даёт мало практической пользы, но увеличивает количество secrets, lifecycle paths, reconciliation logic и возможных отказов конфигурации.

DigitalHouses сознательно выбирает здесь **KISS и operational adoption** вместо enterprise-style privilege segmentation.

Цель — чтобы подключённый USB UPS действительно был настроен, контролировался и безопасно выключал PVE при аварии, а не оставался годами только физически подключённым кабелем без работающей automation.

Это осознанное risk acceptance для указанного scope, а не универсальное правило для всех NUT deployments.


## Provisioning baseline

NUT является обязательной частью стандартной конфигурации DigitalHouses PVE и устанавливается **всегда**, независимо от того, подключён UPS в момент provisioning или нет.

Ansimble заранее подготавливает:

- NUT packages;
- локальный NUT server на loopback;
- managed user `dh_primary_user`;
- persistent generated secret;
- конфигурацию command plane, необходимую DigitalHouses PVE Agent;
- стандартное имя локального UPS `ups`;
- Agent configuration для локального NUT endpoint.

Отсутствие физического UPS является допустимым runtime state и не должно требовать повторного provisioning.

Целевое эксплуатационное свойство: если совместимый локальный USB UPS подключён позже, базовую NUT/PVE Agent control-plane конфигурацию повторно создавать не требуется. Hardware-specific runtime discovery и activation должны происходить поверх уже подготовленного baseline.

Emergency shutdown policy остаётся отдельным gate и не включается только вследствие появления UPS.
