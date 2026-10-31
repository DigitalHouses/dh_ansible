# Fresh Ansible runner: serzhan-lab (Jonsbo)

**Статус на 2026-10-31:** подготовлен код, но destructive переустановка
CT1231 на новом PVE **ещё не выполнялась**. В private Fleet создан
`inventories/serzhan-lab` с локальным runner, PVE-метаданными и
неразрушаемым предварительным описанием VM2232.

## Границы

- Новый site ID: `serzhan-lab`, owner `serzhan`, type `lab`.
- Новый Ansible: CT1231, `ansible-serzhan-lab`, адрес последнего
  октета `.231` от реальной сети PVE `vmbr0`.
- Будущий Media Server: VM2232, `192.168.11.232/24`.
- PVE сохраняет **фактический** адрес, не вычисляемый по site/VMID.
- HAOS VM2253, другие гости и физические диски не изменяются.
- Старый runner CT1231, `ansible-serzhan-home`, и **только он**
  может быть удалён после точной проверки hostname, IP, rootfs,
  unprivileged и отсутствия внешних mount points.
- Не переносить старые сертификаты, GitHub OAuth, Fleet Vault password,
  SSH private keys или PVE root-worker из `serzhan-home`.
  Новому site нужны собственные credentials и первая авторизация.

## Одноразовое создание

В Shell фактического PVE под root скачать
`bootstrap/replace-site-runner.sh` по **закреплённому SHA** и вызвать:

```bash
bash replace-site-runner.sh serzhan-home serzhan-lab <verified-40-char-sha> <actual-PVE-IP>
```

Скрипт:
1. Подтверждает реальный PVE IP, доступность Debian 13 template и
   идентичность исключительно CT1231.
2. Удаляет только проверенный CT1231 с его rootfs.
3. Вызывает штатный fresh `bootstrap/install.sh` с site `serzhan-lab`.
4. Проверяет hostname, IP нового LXC и локальную запись фактического PVE.
5. Выводит `FRESH_RUNNER=PASS` и адрес Semaphore.

Никакого удаления VM2253, VM2232 или raw disks в этом сценарии нет.
При несоответствии old CT проверкам — `BLOCKED`, **не пытаться**
подгонять значения командой наугад.

## Первичный запуск

1. Открыть новый Semaphore `https://192.168.11.231:3000/`;
   начальный пароль читать **только локально** с PVE с помощью
   `pct exec 1231 -- cat /root/dh-semaphore-initial-admin-password`.
2. Запустить №00 для получения каталога.
3. Запустить №05 и подтвердить GitHub Device Flow, затем №06
   с master password Fleet Vault (Secret). После удаления CT старые
   локальные credentials не остаются.
4. Запустить №40 и привязать новое устройство Remote Desktop Commander.
   Старое подключение `ansible-serzhan-home` перестанет работать.
5. Выполнить [общее подключение PVE](pve-native-commissioning.md);
   после этого №50 выполняет read-only аудит без legacy worker.
6. По живому read-only аудиту устройств дополнить `serzhan-lab/media_vm.yml`
   реальными дисками/WWN и PCI. До этого `approvals.provision: false`,
   запуск №80 запрещён. №80/81 в текущем каталоге всё ещё зависят
   от прежнего PVE worker и нуждаются в отдельной миграции.

`serzhan-home` остаётся отдельным объектом Fleet (VM2238/.238);
старые диски и UUID из него не использовать на Jonsbo.
