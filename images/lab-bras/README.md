# Lab BRAS cloud-init 0.1.0

Первичная настройка отдельной Ubuntu Server 24.04 LTS VM в Proxmox: Accel-PPP,
общий PPPoE-логин, пул адресов, DNS для клиентов, IPv4 forwarding и NAT.
**Пакеты не устанавливаются и не обновляются.** Это самостоятельный сервер,
не образ `lab-uplink`, который работает внутри GNS3.

## Что подготовить в образе

- Установленные Accel-PPP с модулями `pppoe`, `ippool`, `chap-secrets`,
  `auth_pap`, `auth_chap_md5`, `auth_mschap_v2`, `log_file`.
- `cloud-init`, Netplan с networkd, `nftables`, `iproute2`, `kmod`, `procps`,
  `qemu-guest-agent`. Настроенный datasource NoCloud для Proxmox.
- По умолчанию бинарник Accel-PPP: `/usr/sbin/accel-pppd`. Проверьте
  `command -v accel-pppd`; другой путь передайте через `--binary`.
- Доступ администратора по SSH-ключу или консоли. Snippet не создаёт пользователя
  и не меняет SSH; при `cicustom user=...` настройки пользователя из GUI Proxmox
  не добавляются автоматически. При необходимости добавьте `users` и
  `ssh_authorized_keys` в сгенерированный cloud-config перед запуском.
- Это выделенная ВМ: другой маршрутизатор, UFW или служба, перезагружающая
  nftables, не должны управлять её forwarding. Имеющиеся input-правила следует
  согласовать отдельно; доступ управления через WAN этот рецепт не ограничивает.

Штатная служба `accel-ppp.service`, если есть, останавливается и отключается.
Вместо неё используется `lab-bras.service` с явным конфигом и запуском в foreground.
Конфигурация `/etc/accel-ppp.conf` заменяется. Модули должны быть доступны бинарнику
из его стандартного каталога; особенности вашей сборки проверяются на ВМ.

## Сеть

```text
internet / management ─ net0 / ens18 (DHCP, default route)
                           Ubuntu BRAS
PPPoE VLAN           ─ net1 / ens19 (без IP, PPPoE listener)
```

На Proxmox задайте `net1` тег `INET_VLAN` на нужном мосту. В госте VLAN уже
untagged: VLAN-подинтерфейс не создаётся. MAC этой карты укажите в `BRAS_MACS`
в конфигурации кластера и установите `lab-uplink-filter` на всех нодах со слотами
по `../../install/README.md`. Не меняйте MAC BRAS без обновления фильтра.

Проверьте имена карт в образе (`ip -br link`); `ens18`/`ens19` — только значения
по умолчанию. Внешняя сеть должна давать DHCP и шлюз. Для статического WAN
измените `network-config.yaml`: `dhcp4: false`, `addresses`, `routes` и
`nameservers` по формату cloud-init network v2. С `cicustom network=...`
параметры `ipconfig0` в GUI не определяют сеть.

Пул по умолчанию `10.254.0.0/24`: адрес сервера `.1`, клиенты `.2–.254`.
Выберите другой `--pool`, если он пересекается с WAN, сетью управления или лабами.
Все клиенты используют один логин, ограничение одной сессией не включается.
Сервер выдаёт DNS `1.1.1.1` и `9.9.9.9` (меняются через `--dns`).
Разрешены PPP → WAN и ответы; PPP → PPP запрещён. Доступ к самой ВМ из PPP
разрешён для ICMP и ответов на её соединения, остальные подключения запрещены.
IPv6 по PPP отключён. NAT в `lab-uplink` и NAT на BRAS дают двойной NAT.

## Сгенерировать snippets

На рабочей машине с Python 3.9+ (без сторонних библиотек):

```sh
python3 images/lab-bras/build_cloud_init.py \
  --user lab-uplink \
  --wan ens18 --access ens19 \
  --pool 10.254.0.0/24 \
  --output-dir /tmp/lab-bras-snippets
```

Пароль вводится дважды без эха и без аргумента командной строки. Используйте
**те же логин и пароль**, что при подготовке клиентского `lab-uplink.qcow2`.
Каталог должен быть новым: генератор не перезаписывает существующий результат.
Файлы записываются с правами `0600`, каталог — `0700`. Несмотря на расширение
`.yaml`, содержимое — JSON, который является допустимым YAML для cloud-init.
`user-data.yaml` начинается с обязательной строки `#cloud-config`.

Скопируйте оба файла на Proxmox в хранилище с типом содержимого `snippets`.
Например, при стандартном directory storage `local`:

```sh
install -d -m 0700 /var/lib/vz/snippets/lab-bras
install -m 0600 /path/to/user-data.yaml /var/lib/vz/snippets/lab-bras/user-data.yaml
install -m 0600 /path/to/network-config.yaml /var/lib/vz/snippets/lab-bras/network-config.yaml

# 9100 — пример VMID; cloud-init drive добавлять только если его ещё нет.
qm set 9100 --ide2 local-lvm:cloudinit
qm set 9100 --cicustom 'user=local:snippets/lab-bras/user-data.yaml,network=local:snippets/lab-bras/network-config.yaml'
qm set 9100 --agent enabled=1
qm start 9100
```

Исправьте VMID, storage, мосты и VLAN под стенд. Перед стартом проверьте обе
карты ВМ в Proxmox. Для миграции snippets должны быть доступны на целевой ноде.
Настройка выполняется на **первой загрузке новой ВМ**. Для клонируемого шаблона
заранее очистите состояние cloud-init при его подготовке; не запускайте
`cloud-init clean` на работающем BRAS с активными клиентами.

Секрет хранится в user-data, cloud-init drive, кэше cloud-init гостя и в
`/etc/ppp/lab-bras-secrets` (`0600`). Не добавляйте snippets в Git, не публикуйте
дампы cloud-init и ограничьте доступ к дискам, резервным копиям и storage.

## Проверить после первого старта

Внутри ВМ:

```sh
cloud-init status --wait
systemctl status lab-bras lab-bras-firewall --no-pager
journalctl -u lab-bras -b --no-pager -n 40
tail -n 40 /var/log/accel-ppp/lab-bras.log
ip -br address
sysctl net.ipv4.ip_forward
nft list table inet lab_bras
```

Если образ неполон, init завершится ошибкой; пакетный менеджер не вызывается.
После исправления причины `/usr/local/sbin/lab-bras-init` можно запустить вручную:
он повторно загружает свою таблицу, не очищая весь ruleset, и перезапускает сервер,
**разрывая активные PPPoE-сессии**. При перезагрузке ВМ настройки сохраняются
через systemd и sysctl, cloud-init повторно не требуется.

На стенде проверьте discovery → authentication → адрес из пула, затем DNS и
HTTPS из лабы, большие ответы (MTU/MRU 1492, MSS clamp уже есть у клиента),
две одновременные лабы с общим логином, изоляцию PPP-клиентов и восстановление
после перезагрузки BRAS. Сквозной запуск Accel-PPP/Proxmox в среде разработки
не выполняется.

Офлайн-проверки генератора:

```sh
python3 -B -m unittest discover -s images/lab-bras -p 'test_*.py' -q
```
