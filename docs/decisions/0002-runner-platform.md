# ADR-0002 — Runner platform: Debian 13 unprivileged LXC with nesting

Статус: **Accepted**. Дата: 2026-10-05.

## Контекст

Legacy CT2900 использует Debian 13 unprivileged LXC без `nesting` и находится в состоянии `degraded` с массовыми ошибками systemd. Копировать его профиль нельзя.

Новый runner должен:
- быть лёгким для небольших PVE-host;
- использовать поддерживаемый текущий `ansible-core`;
- не требовать отдельного Python runtime;
- иметь минимальный набор LXC capabilities;
- проходить строгий OS acceptance до установки инфраструктурных credentials.

## Решение

Использовать:

- Debian 13 (trixie);
- unprivileged LXC;
- `features: nesting=1`;
- без `keyctl`, `fuse`, `mknod`, дополнительных mount features и privileged mode;
- 1 vCPU;
- 1024 MiB RAM;
- 512 MiB swap;
- 16 GiB thin-provisioned root disk;
- статический IPv4 из site inventory;
- `onboot=0` и `protection=1` до полной приёмки;
- после acceptance — отдельный lifecycle apply для `onboot`/startup policy.

## Почему Debian 13

Текущий стабильный `ansible-core 2.21` поддерживает control-node Python 3.12–3.14. Debian 13 предоставляет Python 3.13 штатно. Debian 12 с Python 3.11 потребовал бы дополнительного runtime или удержания старого Ansible, что создаёт лишнюю зависимость.

## Почему nesting=1

Современный systemd в Debian 13 требует возможности создавать namespaces/cgroup-related isolation внутри LXC. Proxmox прямо указывает, что для таких контейнеров может требоваться nesting. Legacy CT2900 без nesting является фактическим отрицательным примером.

Это осознанный security trade-off: nesting расширяет доступ контейнера к части host procfs/sysfs. Поэтому runner остаётся **unprivileged**, а остальные LXC features не включаются без отдельной необходимости.

## Почему не VM

VM даёт более сильную границу изоляции, но для текущего домашнего PVE добавляет лишний RAM/CPU/storage overhead и усложняет bootstrap. Runner уже получает site-scoped infrastructure credentials, поэтому основной security boundary здесь — least privilege самих credentials, unprivileged container и минимальные capabilities.

Если будущий threat model потребует отделить runner от PVE kernel, профиль может быть заменён VM через отдельный ADR; site inventory не должен от этого менять смысл.

## Runtime Ansible

Первый pin:
- `ansible-core==2.21.4`

Устанавливать в отдельный Python virtual environment. Не полагаться на distro Ansible package и не устанавливать prerelease без отдельного решения.

## Acceptance gate

До Git deploy key, Vault material, PVE/TrueNAS credentials и любых write operations:

1. `systemctl is-system-running` = `running`;
2. `systemctl --failed` = 0;
3. journald, tmpfiles, networking, DNS и time sync исправны;
4. hostname/machine-id/IP совпадают с inventory;
5. reboot acceptance PASS;
6. только после этого устанавливается pinned Ansible runtime.

Fail gate => исправляем профиль/совместимость, а не добавляем service-specific обходы.
