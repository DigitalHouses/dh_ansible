# Native NetAlertX · миграция из legacy (экспериментальная роль)

Эта общая роль перенесена из `DigitalHouses/ansible` для дальнейшей
проверки и адаптации. Исторический baseline: v26.5.4,
`f894390dfab65c4c8fc605af6ee3fa08d95bd354`, Debian 13,
Python venv, systemd, nginx/PHP, SQLite, штатный ARPSCAN. Docker не нужен.

**Не вызывать эту роль напрямую в production.** У Амира на CT301 была
проверена ручная нативная установка; legacy role не проходила
`clean-install E2E` и `changed=0`. Перенос файлов не означает приёмку.

Пока нет общей guest-provisioning роли и защищённого доступа
Ansible к новому LXC, эта роль намеренно не включена в задачу Semaphore 60.
`netalertx_native_approved` по умолчанию `false`, preflight требует
установленную и проверенную identity (hostname и machine-id).

Прежде чем связать роль с задачей 60: провести независимые тесты
provision/adopt, DHCP/IP-conflict, защитить доступ к GraphQL TCP/20212,
проверить идемпотентность, стабильность и сохранность конфигурации/SQLite.
CT333 (192.168.11.39) не является объектом управления роли.
