# Развёртывание тестового стенда: агент, слот, менеджер

`lab-provision` запускается от root на ноде Proxmox. Он генерирует все секреты в один каталог
на CephFS и раскладывает их через `pct push` / `pct exec` туда, где их уже ждут агент, менеджер
и слот. Код компонентов и пути внутри контейнеров не меняются. В этом режиме всё живёт на одной
ноде: агент, LXC менеджера и LXC слотов. Moodle и плагин уже установлены.

Шаги выполняются по одному, каждый можно повторить. Уже созданные секреты не пересоздаются.

## Раскладка CephFS

```
/mnt/pve/cfs/                       CEPH_ROOT, точка монтирования CephFS
├── gns3-ro/                        git checkout репозитория (main); root, без записи для group/other
│   ├── agent/                      ставится на ноду (шаг agent)
│   ├── manager/ (+ venv/)          → LXC менеджера: /srv/gns3-ro/manager (RO)
│   ├── slot/    (+ venv/)          → слот: /srv/gns3-ro/slot (RO, ставит агент)
│   └── labs/                       → LXC менеджера: /srv/gns3-ro/labs (RO)
├── gns3-rw/students/               каталоги студентов → слот: /opt/gns3/projects (ставит агент)
└── secrets/                        root 0700, никуда не монтируется
    ├── lab.conf                    настройки стенда (из install/lab.conf.example)
    ├── ca/                         CA: manager-client, node-server, web (тест: ключи здесь)
    ├── nodes/<нода>/               server.crt server.key manager-ca.crt → /etc/lab-node-agent/
    ├── manager/                    manager.env sites.json slots.json manager.{crt,key}
    │                               node-server-ca.crt pve-root-ca.pem → /etc/lab-manager/
    │   └── nginx/                  gw.{crt,key} lab.conf → /etc/nginx/
    ├── slots/<vmid>/               slot.env, токены → /etc/lab-slot/slot.env
    └── moodle/                     web-ca.crt (доверие Moodle и браузеров), plugin.txt
```

`manager.env`, `sites.json`, `slots.json`, `slot.env` и `nginx/lab.conf` собираются заново при каждом
`secrets` и `manager`: правьте `lab.conf`, а не эти файлы. Дополнительные строки `manager.env`
кладутся в `secrets/manager/manager.env.local`, каталог образов для лаб из Moodle — в
`secrets/manager/images.json` (образец — `manager/examples/images.json`).

## Что должно быть до начала

- Нода в кластере, CephFS смонтирован в `CEPH_ROOT`.
- Репозиторий склонирован в `gns3-ro` с `umask 022`: агент не принимает каталоги с записью для
  group/other. Если клон уже есть: `chmod -R go-w /mnt/pve/cfs/gns3-ro`.
- LXC слота: непривилегированный, стандартный idmap, статический IP в `net0`, Ubuntu 24.04 и
  GNS3 2.2.61 внутри (готовый шаблон). Точки `mp0`/`mp1` слота принадлежат агенту, образы GNS3 —
  на `mp2` и дальше или в rootfs.
- LXC менеджера: непривилегированный Ubuntu 24.04, статический IP. Пакеты (`postgresql`,
  `nginx`, `python3.12-venv`) и пользователя `lab-manager` скрипт поставит сам.
- Доступ в интернет (или зеркало) для `apt` и `pip` из LXC и с ноды.
- DNS или `/etc/hosts`: `PUBLIC_NAME` указывает на IP LXC менеджера для сервера Moodle и для
  браузеров. Имя агента `<нода>.<AGENT_DOMAIN>` скрипт сам добавит в `/etc/hosts` менеджера.

## Шаги

```sh
cd /mnt/pve/cfs/gns3-ro && git pull
install -d -m 0700 /mnt/pve/cfs/secrets
cp install/lab.conf.example /mnt/pve/cfs/secrets/lab.conf && chmod 600 /mnt/pve/cfs/secrets/lab.conf
nano /mnt/pve/cfs/secrets/lab.conf          # VMID, IP, имена, адрес Moodle

P=/mnt/pve/cfs/gns3-ro/install/lab-provision
$P secrets          # CA, сертификаты, токен Proxmox, секрет сайта, токены слотов, конфиги
$P venv slot        # venv слота в gns3-ro/slot/venv (собирается в первом слоте, слот выключен)
$P slot             # слот: тег labslot, slot.env, lab-slot-init, lab-slot.service, проверки
$P agent            # каталоги gns3-rw, сертификаты агента, диапазон VMID, uid/gid, install-node.sh
$P venv manager     # venv менеджера в gns3-ro/manager/venv (в LXC менеджера)
$P manager          # монтирования, пакеты, БД, секреты, migrate, шлюз, nginx, сервисы
$P moodle           # web CA в Moodle (если MOODLE_VMID задан), настройки для плагина
$P check            # агент, слоты, менеджер, связь менеджер → агент и Proxmox
$P enable           # слоты из maintenance в free
```

После `enable` запустите лабу из Moodle. Если сессия падает, смотрите журналы:
`pct exec <MANAGER_VMID> -- journalctl -u lab-manager -n 100`, `journalctl -u lab-node-agent -n 50` на
ноде, `pct exec <слот> -- journalctl -u lab-slot -n 50` (пока слот запущен).

### Что делает каждый шаг

- **secrets** — три CA: `manager-client-ca` (клиент менеджера → агент), `node-server-ca` (агент),
  `web-ca` (nginx для Moodle и браузеров). Сертификаты: агент на `<нода>.<AGENT_DOMAIN>` и `NODE_IP`,
  клиент `lab-manager`, nginx на `PUBLIC_NAME`. Токен `lab-manager@pve!manager` с ролями и пулом
  `lab-slots` (как в `manager/README.md` §5). Случайные секрет сайта, ключ шлюза, API- и
  консольный токены каждого слота.
- **venv slot / venv manager** — на время сборки каталог `venv/` монтируется в LXC на запись
  (`mp2` и дальше), венв собирается Python 3.12 этого LXC по тому же пути, что видят сервисы, потом
  монтирование снимается, а `venv/` возвращается root. Повторять только при изменении
  `requirements.txt`.
- **slot** — работает только с выключенным слотом: снимает `mp0`/`mp1` (их ставит агент при каждом
  старте), временно запускает слот, кладёт файлы через `pct push`, проверяет Ubuntu, Python,
  GNS3, образы, `/dev/kvm`, читает uid/gid `gns3` и выключает слот.
- **agent** — создаёт `gns3-rw/students/placeholder`, проверяет права предков, кладёт
  сертификаты в `/etc/lab-node-agent`, пишет override (`LAB_CEPH_ROOT`, `LAB_CODE_DIR`,
  `LAB_STUDENTS_DIR`, диапазон VMID по `SLOTS`, uid/gid `gns3` + 100000) и запускает
  `agent/install-node.sh`. Firewall для 8443 скрипт только подсказывает.
- **manager** — монтирует `gns3-ro/manager` и `gns3-ro/labs` (RO; если `labs/` нет, берёт пример
  из `manager/examples/labs`), ставит пакеты, создаёт пользователя и БД, кладёт секреты с нужными
  владельцами, выполняет `migrate`, `gateway-env`, ставит unit'ы и nginx (`/api/` только для
  `MOODLE_IP`, `/console/` для браузеров), ждёт `readyz`.
- **moodle** — кладёт `web-ca.crt` в системное хранилище доверия LXC Moodle
  (`update-ca-certificates`), при необходимости добавляет `PUBLIC_NAME` в его `/etc/hosts` и
  проверяет `https://PUBLIC_NAME/api/v1/info` с секретом сайта. Печатает значения для плагина
  (`secrets/moodle/plugin.txt`). Если Moodle не LXC на этой ноде, сертификат нужно поставить вручную.

## Ограничения тестового режима

- Ключи CA лежат в `secrets/ca`. Перед рабочим режимом унесите их офлайн и **перевыпустите всё**:
  сертификаты, токен Proxmox, секрет сайта, токены слотов. Удалить или скрыть каталог недостаточно,
  он остаётся в снапшотах и бэкапах CephFS.
- Браузерам студентов тоже нужно доверие к `web-ca.crt` (консоли идут по `wss://PUBLIC_NAME`).
  Для широкого теста лучше положить в `secrets/manager/nginx/gw.{crt,key}` сертификат от
  настоящего CA: скрипт не перезаписывает существующие файлы.
- Чтобы перевыпустить один сертификат или токен, удалите его файл в `secrets/` и повторите
  `secrets`, затем шаги, которые его раскладывают (`agent`, `manager`, `slot`, `moodle`).
- Firewall не настраивается: 8443 на ноде (только менеджер) и 8080 слота (только менеджер) закройте
  сами.
- Склонированные слоты нужно уникализировать до `slot` (machine-id, SSH-ключи, `/var/lib/lab-slot`).
- `git pull` в `gns3-ro` делайте без активных сессий, затем
  `pct exec <MANAGER_VMID> -- systemctl restart lab-manager lab-console-gateway`.
