# Развёртывание тестового стенда: агент, слот, менеджер

Подготовка неизменяемого выпуска выполняется отдельно: [`RELEASES.md`](RELEASES.md),
команды `lab-provision release build|verify|list`. Публикация выпуска не меняет
работающие компоненты. Описанный ниже прежний порядок установки сохраняется.

`lab-provision` запускается от root на нодах кластера Proxmox. Он генерирует все секреты в один
каталог на CephFS и раскладывает их через `pct push` / `pct exec` туда, где их уже ждут агенты,
менеджер и слоты. Код компонентов и пути внутри контейнеров не меняются. На каждой ноде со слотами
работает свой агент, LXC менеджера живёт на одной из нод. Moodle и плагин уже установлены.

Шаги выполняются по одному, каждый можно повторить. Уже созданные секреты не пересоздаются.
`pct` видит только контейнеры своей ноды, поэтому у каждого шага есть место запуска:

| Шаг | Где запускать |
|---|---|
| `secrets` | на любой ноде кластера |
| `venv slot`, `slot`, `agent` | на каждой ноде со слотами (работают со слотами этой ноды) |
| `venv manager`, `manager`, `enable` | на ноде LXC менеджера |
| `moodle` | на ноде LXC Moodle (если Moodle не LXC, на любой) |
| `check` | на нодах со слотами (агент через loopback) и на ноде менеджера (менеджер, все агенты и слоты) |

Ноду и IP каждого контейнера скрипт берёт из `/etc/pve/nodes/<нода>/lxc/<vmid>.conf` (строка `net0`):
его видит любая нода, так что в `lab.conf` перечисляются только ноды (`NODES`) и VMID (`SLOTS`).

## Раскладка CephFS

```
/mnt/pve/cfs/                       CEPH_ROOT, точка монтирования CephFS
├── gns3-ro/                        git checkout репозитория (main); root, без записи для group/other
│   ├── agent/                      ставится на ноду (шаг agent)
│   ├── manager/ (+ venv/)          → LXC менеджера: /srv/gns3-ro/manager (RO)
│   ├── slot/    (+ venv/)          → слот: /srv/gns3-ro/slot (RO, ставит агент)
│   └── labs/                       → LXC менеджера: /srv/gns3-ro/labs (RO)
├── gns3-rw/students/               каталоги студентов → слот: /opt/gns3/projects (ставит агент)
├── gns3-images/                    IMAGES_DIR: QEMU/, IOU/, прошивки → слот: /opt/gns3/images (RO, ставит агент)
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
`secrets` и `manager`: правьте `lab.conf`, а не эти файлы. Лимиты размещения лаб на ноде
(`NODE_GUEST_MEMORY_MB`, `NODE_CPU_OVERCOMMIT`) тоже задаются в `lab.conf` и попадают в `manager.env`. Дополнительные строки `manager.env`
кладутся в `secrets/manager/manager.env.local`, каталог образов для лаб из Moodle — в
`secrets/manager/images.json` (образец — `manager/examples/images.json`).

## Что должно быть до начала

- Нода в кластере, CephFS смонтирован в `CEPH_ROOT`.
- Репозиторий склонирован в `gns3-ro` с `umask 022`: агент не принимает каталоги с записью для
  group/other. Если клон уже есть: `chmod -R go-w /mnt/pve/cfs/gns3-ro`.
- LXC слотов создаёте вы сами (клоны одного шаблона) на тех нодах, где они нужны: непривилегированные,
  стандартный idmap, статический IP в `net0`, Ubuntu 24.04 и GNS3 2.2.61 внутри. uid/gid `gns3`
  должны совпадать во всех слотах всех нод: каталоги студентов общие. Точки `mp0`/`mp1` слота
  принадлежат агенту; с `IMAGES_DIR` и `mp2` тоже (образы), без него образы — в rootfs слота.

### Образы GNS3 на CephFS (`IMAGES_DIR`)

Один каталог на всех: агент монтирует `<CEPH_ROOT>/<IMAGES_DIR>` в `/opt/gns3/images` каждого слота
только на чтение. Структура — как в `/opt/gns3/images` слота (`QEMU/`, `IOU/`, прошивки рядом).
Внутри непривилегированного CT файлы видны как `nobody`, поэтому:

```sh
I=/mnt/pve/cfs/gns3-images
cp -a /путь/к/образам/. $I/                       # или rsync из /opt/gns3/images эталонного слота
chown -R root:root $I && chmod -R go-w,o+rX $I && chmod 0755 $I/IOU/*.bin
cd $I && find . -type f ! -name '*.md5sum' -exec sh -c 'md5sum "$1" | cut -d" " -f1 >"$1.md5sum"' _ {} \;
```

`.md5sum` GNS3 пишет сам рядом с образом; на RO-монтировании не может и считает хеш при каждом
использовании, поэтому их кладём заранее (повторить для новых образов). Новый образ появляется во
всех слотах сразу, без перезапуска; заменять существующий — без активных сессий.
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
$P secrets          # CA, сертификаты всех нод, токен Proxmox, секрет сайта, токены слотов, конфиги
$P venv slot        # venv слота в gns3-ro/slot/venv (в первом слоте этой ноды, слот выключен); один раз
# на каждой ноде со слотами:
$P slot             # слоты ноды: тег labslot, slot.env, lab-slot-init, lab-slot.service, проверки
$P agent            # каталоги gns3-rw, сертификаты агента, диапазон VMID, uid/gid, install-node.sh
# на ноде менеджера:
$P venv manager     # venv менеджера в gns3-ro/manager/venv (в LXC менеджера)
$P manager          # монтирования, пакеты, БД, секреты, /etc/hosts, migrate, шлюз, nginx, сервисы
$P moodle           # web CA в Moodle (если MOODLE_VMID задан), настройки для плагина
$P check            # агент ноды, менеджер, связь менеджер → все агенты, слоты и Proxmox
$P enable           # слоты из maintenance в free (или только перечисленные: enable 2101 2102)
```

### Добавить ноду со слотами

Venv слота общий и собран под тот же шаблон, `venv slot` не повторяется. Сессии на других нодах
не мешают, кроме шага `manager`: он перезапускает менеджер.

```sh
# 1. Склонируйте слоты на новой ноде (уникальный статический IP, см. «Ограничения»), выключенными.
# 2. В secrets/lab.conf: NODES="pve1=10.10.12.9 pve2=10.10.12.10", SLOTS="2001 2002 2101 2102".
$P secrets          # любая нода: сертификат pve2, токены 2101/2102, manager.env и slots.json со всеми
$P slot             # на pve2: только слоты pve2
$P agent            # на pve2: агент ноды pve2, затем закройте 8443 firewall'ом
$P manager          # на ноде менеджера, без активных сессий: новые slots.json, PVE_NODES, /etc/hosts
$P check            # на ноде менеджера: видно агент pve2 и его слоты
$P enable 2101 2102 # на ноде менеджера: только новые слоты
```

После `enable` запустите лабу из Moodle. Если сессия падает, смотрите журналы:
`pct exec <MANAGER_VMID> -- journalctl -u lab-manager -n 100`, `journalctl -u lab-node-agent -n 50` на
ноде, `pct exec <слот> -- journalctl -u lab-slot -n 50` (пока слот запущен).
Разбор кодов (`NO_SLOTS` и другие) — [`TROUBLESHOOTING.md`](TROUBLESHOOTING.md).

### Что делает каждый шаг

- **secrets** — три CA: `manager-client-ca` (клиент менеджера → агент), `node-server-ca` (агенты),
  `web-ca` (nginx для Moodle и браузеров). Сертификаты: агент каждой ноды из `NODES` на
  `<нода>.<AGENT_DOMAIN>` и её IP, клиент `lab-manager`, nginx на `PUBLIC_NAME`. Токен `lab-manager@pve!manager` с ролями и пулом
  `lab-slots` (как в `manager/README.md` §5). Случайные секрет сайта, ключ шлюза, API- и
  консольный токены каждого слота. `slots.json` — все слоты из `SLOTS`, `PVE_NODES` — все ноды из `NODES`.
- **venv slot / venv manager** — на время сборки каталог `venv/` монтируется в LXC на запись
  (`mp2` и дальше), венв собирается Python 3.12 этого LXC по тому же пути, что видят сервисы, потом
  монтирование снимается, а `venv/` возвращается root. Повторять только при изменении
  `requirements.txt`.
- **slot** — работает только с выключенным слотом: снимает `mp0`/`mp1` (их ставит агент при каждом
  старте), с `IMAGES_DIR` ставит `mp2` с образами, пересобирает `slot.env` по текущему IP в `net0`,
  временно запускает слот, кладёт файлы через `pct push`, проверяет Ubuntu, Python,
  GNS3, образы, `/dev/kvm`, читает uid/gid `gns3` и выключает слот.
- **agent** — создаёт `gns3-rw/students/placeholder` (и `IMAGES_DIR` с `QEMU/`, `IOU/`), проверяет права предков, кладёт
  сертификаты ноды в `/etc/lab-node-agent`, сверяет uid/gid `gns3` со слотами других нод, пишет
  override (`LAB_CEPH_ROOT`, `LAB_CODE_DIR`, `LAB_STUDENTS_DIR`, `LAB_IMAGES_DIR`, диапазон VMID — от меньшего до
  большего VMID слотов этой ноды, uid/gid `gns3` + 100000) и запускает
  `agent/install-node.sh`. Firewall для 8443 скрипт только подсказывает.
- **manager** — монтирует `gns3-ro/manager` и `gns3-ro/labs` (RO; если `labs/` нет, берёт пример
  из `manager/examples/labs`), ставит пакеты, создаёт пользователя и БД, добавляет в `/etc/hosts`
  `<IP> <нода>.<AGENT_DOMAIN> <нода>` каждой ноды (существующие строки не трогает, о расхождении
  предупреждает), кладёт секреты с нужными
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
- Firewall не настраивается: 8443 на каждой ноде со слотами (только менеджер) и 8080 слотов (только
  менеджер) закройте сами.
- Склонированные слоты нужно уникализировать до `slot` (machine-id, SSH-ключи, `/var/lib/lab-slot`).
- `git pull` в `gns3-ro` делайте без активных сессий, затем
  `pct exec <MANAGER_VMID> -- systemctl restart lab-manager lab-console-gateway`.
