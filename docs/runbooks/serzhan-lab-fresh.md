# Fresh Ansible runner — serzhan-lab (Jonsbo)

Этот документ описывает **отдельный** site `serzhan-lab`. Для сохранения site `serzhan-home` при повторной установке в Bootstrap следует указать именно `serzhan-home`.

## Правило: удаление и установка — разные операции

**Стандартный** `bootstrap/install.sh` не удаляет и не перезаписывает LXC/VM. При существующем CT/VM 1231 он возвращает `BLOCKED`. Никаких автоматических `pct stop`, `pct destroy`, очистки диска или миграции credentials в нём нет.

Если владелец **отдельно** разрешил уничтожение именно CT1231, оператор предварительно проверяет на PVE его hostname, IP, rootfs, mounts и чужие зависимости, после чего вручную удаляет его из PVE UI либо отдельными командами Proxmox. **Никакого сценария совмещённого удаления и установки нет.** Не удалять VM2253 и другие гости.

После того как `pct config 1231` больше не находит LXC, а `qm config 1231` не находит VM, выполняется **только стандартный Bootstrap**, закреплённый по проверенному commit. Актуальный источник команды — [README](../../README.md#единая-точка-входа--tiny-bootstrap); старые replacement-скрипты запрещены.

При установке `serzhan-lab` получают новый CT1231 `ansible-serzhan-lab`, IP `192.168.11.231/24` для PVE с `vmbr0` в сети 192.168.11.0/24. Новая установка: Debian 13 → pinned Git → Ansible + Semaphore → только задача №00. Старые сертификаты, токены, база Semaphore, Vault, SSH-ключи **не переносятся**.

## Приёмка

После `BOOTSTRAP_FRESH_DONE=PASS` проверить `pct exec 1231 -- semaphore version`, `pct exec 1231 -- systemctl is-active digitalhouses-semaphore.service`, HTTP(S) доступность интерфейса, единственную №00. Начальный пароль читается локально на PVE:

```bash
pct exec 1231 -- cat /root/dh-semaphore-initial-admin-password
```

№00 запускается вручную в Semaphore. Новые credentials объекта создаются по утверждённому ADR-0021 отдельно после установки. Это не разрешает автоматическое развёртывание Media Server/VM2232; HAOS VM2253 и остальные гости остаются вне изменений.
