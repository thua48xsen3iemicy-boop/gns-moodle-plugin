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
| `venv slot`, `slot`, `snapshot`, `agent` | на каждой ноде со слотами (работают со слотами этой ноды) |
| `venv manager`, `manager`, `enable` | на ноде LXC менеджера |
| `reconcile` | на ноде слота (читает состояние слота у менеджера по ssh, как `deploy`) |
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

### Неизменяемость баз и производных образов

Снятие со штатным выключением гостя требует Manager 0.15.0 (миграция `010_*`)
и Slot 0.14.1; интерфейс — плагин 0.16.1. До запуска нового менеджера примените
миграции обычным способом. Без QGA нужно явное «Сохранить как есть»; у такого
образа будет предупреждение. Ручной `poweroff` роняет лабу, в том числе авторскую.
Проверки на реальном госте перечислены в `docs/derived-images/TASK-2.md` и `TASK-3.md` рядом.

**Файл образа на месте не заменяется: новая версия — новое имя файла и новый id.**
Это относится и к базам: уже существующие проекты студентов используют их как backing.
`d-<12 hex>.qcow2` — производный слой, созданный агентом из авторского запуска.
Его backing — относительное имя исходной базы в том же `QEMU/`, рядом лежит
готовый GNS3 `<имя>.md5sum`; оба файла имеют права `0444`. В слотах каталог остаётся RO.
Повторное снятие создаёт новый файл над той же базой, сохраняя прежний для старых лаб.

**Не удаляйте `d-*.qcow2` вручную.** Даже retired-образ может быть нужен опубликованной
версии лабы или сохранённому проекту студента. При ошибке пакетного снятия могут остаться
файлы, не опубликованные в БД; их учитывает администратор. Автоматической очистки
готовых образов нет. Агент удаляет только устаревшие временные `.part`.

У снимаемых баз в `IMAGES_FILE` задайте `sha256: "sha256:<64 hex>"`, вычисленный
на узле при установке базы (`sha256sum <файл>`). Проверка на узле, только чтение:

```sh
python3 install/check-derived-images.py /mnt/pve/cfs/<IMAGES_DIR>/QEMU \
  --images-file /path/to/images.json
```

Без `--images-file` проверяются цепочки, существование базы, права и MD5-sidecar;
с ним — также хеш баз. Команда выводит JSON и возвращает ненулевой код при ошибке.
Перед включением UI владелец проверяет GNS3 2.2.61: linked clone над производным
образом, три уровня backing-chain, запуск ноды и продолжение в другом слоте
(`docs/derived-images/TASK.md`). Сама проверка файлов этого не доказывает.

### Пустой образ для дополнительных дисков (`blank.qcow2`)

Дополнительные диски нод (Manager 0.22.0, Slot 0.18.0, CONTRACTS §4.2) — связанные клоны одного пустого образа.
Положите его один раз рядом с базами, на любом узле с CephFS:

```sh
qemu-img create -f qcow2 /mnt/pve/cfs/<IMAGES_DIR>/QEMU/blank.qcow2 1M
chmod 0444 /mnt/pve/cfs/<IMAGES_DIR>/QEMU/blank.qcow2
```

Образ без backing file, не больше 1 ГиБ и кратен 1 МиБ; слот растягивает клон до нужного размера сам. Как и базу,
его нельзя удалять или заменять: диски в проектах студентов ссылаются на него как на backing. Только после этого
ставьте `"extra_disks": true` нужным образам в `IMAGES_FILE` (Linux, Windows; только `qemu`) и перезапускайте
менеджер. Сумму дисков одной лабы ограничивает `limits.max_disk_gb` сайта в `sites.json`. Диски лежат в проекте
студента на CephFS и занимают столько, сколько в них записано.

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
$P slot             # слоты ноды: тег labslot, slot.env, lab-slot-init, lab-slot.service, проверки, снимок чистого слота
$P agent            # каталоги gns3-rw, сертификаты агента, диапазон VMID, uid/gid, install-node.sh
# на ноде менеджера:
$P venv manager     # venv менеджера в gns3-ro/manager/venv (в LXC менеджера)
$P manager          # монтирования, пакеты, БД, секреты, /etc/hosts, migrate, шлюз, nginx, сервисы
$P moodle           # web CA в Moodle (если MOODLE_VMID задан), настройки для плагина
$P check            # агент ноды, менеджер, связь менеджер → все агенты, слоты и Proxmox
$P enable           # слоты из maintenance в free (или только перечисленные: enable 2101 2102)
```

`$P check` в конце показывает слоты, которые менеджер держит в карантине (`QUARANTINED slot …`: VMID, сессия,
студент, команда из отметки агента), и предупреждает, если агент тратит на запуск дольше `AGENT_START_TIMEOUT`.
Такой слот сверяют вручную по шагам из `TROUBLESHOOTING.md` («`COMMAND_UNCERTAIN`: слот в карантине»); последний шаг
перед `enable` автоматизирован: `$P reconcile <VMID> [--yes]` на ноде слота.

## Снимок чистого слота

Слот живёт одну сессию: после неё агент откатывает его к снимку `lab-clean` (`CLEAN_SNAPSHOT` в
`lab.conf`, по умолчанию `lab-clean`), и следующий студент получает слот в исходном состоянии, что бы
первый ни сделал в госте. Всё, что принадлежит студенту, лежит в его каталоге на CephFS.

Снимок — это шаблон плюс собственная идентичность слота (токены, TLS, IP, `slot.env`), поэтому его делает
шаг `slot` в самом конце, когда всё это уже внутри. Отдельно, ничего не перекладывая:
`$P snapshot [VMID...]` (слот выключен, в менеджере в `maintenance`, после снимка `enable`).

- **Откат возвращает весь конфиг CT**, не только диск: `cores`, `memory`, `net0`, теги. Ручную правку CT
  (IP, MAC) надо закрепить новым снимком, иначе следующий откат её отменит. Агент и менеджер сами
  проверяют и меняют только свои `mp0`/`mp1`/`mp2`, размер, тег `labslot` и `unprivileged`; остальной
  конфиг слота (сеть, hostname) ваш и живёт в снимке. Размер и точки монтирования менеджер ставит
  заново при каждой сессии.
- **Слот, который остался оператору (`failed`), не откатывается**, чтобы в нём можно было прочитать
  журналы; его откатывает `enable`.
- **Точки монтирования агента в снимок не попадают**: `mp0`, `mp1` и, с `IMAGES_DIR`, `mp2` с образами.
  Шаг убирает их перед снимком по двум причинам. Proxmox не делает снимок CT с bind-монтированием
  (`snapshot feature is not available`), а каталог студента не должен вернуться вместе с откатом. Агент
  ставит все три сам при каждом старте и при каждой заглушке. Своё bind-монтирование на слоте (не
  `mp0`–`mp2` агента) снимок сделать не даст: шаг скажет, какое.
- Повторный `slot` или `snapshot` заменяет снимок. Новую основу слотов (новый шаблон) нужно создавать
  с новым VMID вместе со слотами и снимками.
- `check` показывает у каждого слота ноды, есть ли снимок; без него агент не сможет откатить слот
  (`SNAPSHOT_MISSING`).

## Одной командой: `deploy` и `reset`

`deploy` запускается на любой ноде и проходит все шаги по кластеру. На другие ноды он ходит
по SSH под root, как сам Proxmox (адреса из `/etc/pve/.members`). Скрипт и `lab.conf` должны лежать
на CephFS: каждая нода выполняет тот же `$REPO_DIR/install/lab-provision`.
Недостающие контейнеры он создаёт сам, если они описаны в `lab.conf`:

| Переменная | Что создаётся |
|---|---|
| `SLOT_TEMPLATE="2000=pve1:dhcp"` | шаблон слота (`template`): Ubuntu 24.04, GNS3 2.2.61 в своём venv, QEMU/KVM (`dev0` `/dev/kvm`, `dev1` `/dev/net/tun`; нужен Proxmox VE 8.1+), ubridge, VPCS, библиотеки IOU, пользователь `gns3` с uid/gid `SLOT_GNS3_ID`; без sshd и machine-id |
| `CREATE_MANAGER="pve1:10.10.12.50"` | LXC менеджера `MANAGER_VMID`. PostgreSQL, nginx и службы в нём ставит шаг `manager` |
| `CREATE_SLOTS="2001=pve1:10.10.12.101 …"` | слоты: клоны шаблона на своих нодах (`CT_STORAGE` общий, RBD), свой IP и MAC; по умолчанию полные, `SLOT_CLONE=linked` делает связанные |

Сеть и диски новых CT — `CT_STORAGE`, `CT_BRIDGE`, `CT_CIDR`, `CT_GATEWAY`, `CT_NAMESERVER`, при
необходимости `CT_VLAN` (тег попадает в `net0` слотов, шаблона и менеджера; образец — `lab.conf.example`).

Полный клон копирует весь диск слота, поэтому создание слотов долгое. `SLOT_CLONE=linked` делает связанные
клоны: слот появляется за секунды, потому что делит диск с шаблоном (снимок на RBD). Цена: шаблон нельзя
удалить или пересобрать, пока живы клоны (новую основу делайте шаблоном с новым VMID и пересоздайте слоты;
`reset` удаляет слоты раньше шаблона); клоны остаются на хранилище шаблона. Связанный клон на другую ноду
(`--target`) в сессии не проверялся: проверьте на стенде. Уже созданные слоты опция не меняет. Уже существующий CT не пересоздаётся. Если таких переменных нет,
контейнеры должны быть созданы заранее, как раньше.

```sh
P=/mnt/pve/cfs/gns3-ro/install/lab-provision
$P deploy                       # template, create, secrets, venv slot, slot+agent на каждой ноде,
                                # venv manager, manager, moodle, check, enable
$P reset                        # спросит PUBLIC_NAME; --yes без вопроса
$P reset --secrets              # и новые CA, токены, секрет сайта
$P reset --template             # и шаблон слота (пересобрать GNS3)
```

`reset` нужен, когда данные стенда не нужно сохранять:
- Удаляются LXC менеджера (вместе с базой PostgreSQL) и слотов. CT Moodle скрипт не удаляет никогда.
- На агентах удаляются записи владельцев (`/var/lib/lab-node-agent/<vmid>.json`) удалённых слотов.
  Без этого новая база начала бы эпохи с 1, и агент отвечал бы новому менеджеру `STALE_OWNER`.
  У слотов их SQLite пропадает вместе с CT.
- Каталог `gns3-rw/students` переименовывается в `students.reset-<время>`, а не удаляется:
  удалите его сами, когда он станет не нужен.
- С `--secrets` каталог `secrets` переименовывается так же. Обратно копируются `lab.conf`,
  `manager/images.json`, `manager/manager.env.local`, `slot/iourc` и собственный сертификат nginx
  (не от тестового web CA). После `deploy` в плагин Moodle нужно ввести новый секрет сайта
  (`secrets/moodle/plugin.txt`).

Порядок `reset` → `deploy` — единственный поддерживаемый способ сбросить базу менеджера. Стирать
базу или записи владельцев по отдельности на работающем стенде нельзя
(см. [pilot-hardening](../docs/pilot-hardening/README.md#разбор-неизвестного-исхода)).

Скрипт проверен на имитации кластера из двух нод: заглушки `pct`, `pvesh` и `ssh`, без
настоящего Proxmox. Первый прогон `template` на настоящей ноде нужно проверить глазами:
установка GNS3 из PPA и pip, проброс `/dev/kvm` в непривилегированный CT.

### Свой шаблон слота (вручную)

Если CT для шаблона уже есть и пересоздавать его не хочется, шаг `template` не подойдёт: он не берёт
существующий CT, который не шаблон. Вместо него внутри своего CT запускается `install/slot-template-setup`.
Он делает то же, что `template`: пакеты, пользователь `gns3`, GNS3 2.2.61 в своём venv, конфиг, служба.
CT: непривилегированный Ubuntu 24.04, `nesting=1`, сеть; его VMID вне `SLOTS` и диапазонов слотов.
Скрипт сам откажется работать не в LXC. `dev0`/`dev1` и `pct template` делаются на ноде, скрипт печатает команды.

```sh
V=191                                          # VMID вашего CT
pct push $V /mnt/pve/cfs/gns3-ro/install/slot-template-setup /root/slot-template-setup --perms 0755
pct exec $V -- env GNS3_ID=1500 /root/slot-template-setup install   # GNS3_ID = SLOT_GNS3_ID из lab.conf
pct set $V -dev0 /dev/kvm,gid=<gid группы kvm из вывода>,mode=0660 -dev1 /dev/net/tun,mode=0666
pct shutdown $V && pct start $V                # устройства применяются при старте
pct exec $V -- env GNS3_ID=1500 /root/slot-template-setup verify
pct exec $V -- /root/slot-template-setup clean   # через pct exec, не по ssh: убирает sshd и machine-id
pct shutdown $V && pct template $V
```

Затем в `lab.conf`: `SLOT_TEMPLATE="<VMID>=<нода>:<IP>"` (IP шаблону нужен только формально, он не запускается),
`CREATE_SLOTS`, `CT_STORAGE`, `CT_BRIDGE`, `CT_CIDR`; слоты создаст `lab-provision create`. Шаг `template`
увидит `template: 1` и ничего не пересоберёт.

### Поставить пакеты в слоты

После каждой сессии агент откатывает слот к снимку `lab-clean`. Поэтому пакет, поставленный в работающий
слот, пропадёт после первой же сессии. Сохранить его можно только новым снимком: слот выводят из работы,
ставят пакет в выключенный CT, переснимают и возвращают. Снимок пересоздаёт шаг `slot`: заодно он
проверяет, что GNS3 и QEMU после apt живы. Так удобно поправить один-два слота. Для всех слотов проще
поставить пакет один раз в новый шаблон и пересоздать слоты (см. ниже, «Много слотов»).

Переменные `$MGR` и `$ADM` — как в [`TROUBLESHOOTING.md`](TROUBLESHOOTING.md). Команды `$ADM` выполняются на ноде
менеджера, а `pct` и `$P slot` — на ноде слота.

```sh
P=/mnt/pve/cfs/gns3-ro/install/lab-provision
id=2001
# 1. Вывести из работы (на ноде менеджера). «Slot is in use» — идёт сессия: повторите после неё.
pct exec $MGR -- $ADM slot-maintenance $id --message "ставлю пакеты"
# 2. На ноде слота: CT выключен, точки агента сняты (CephFS в госте не нужен, агент вернёт их сам)
pct status $id                                   # stopped
pct set $id --delete mp0,mp1,mp2                 # mp2 — только если он есть
pct start $id
pct exec $id -- getent hosts archive.ubuntu.com  # слоту нужен выход в интернет (шлюз и DNS в net0)
pct exec $id -- env DEBIAN_FRONTEND=noninteractive apt-get update -qq
pct exec $id -- env DEBIAN_FRONTEND=noninteractive apt-get install -y <пакеты>
pct exec $id -- apt-get clean
pct shutdown $id
# 3. Проверки GNS3, slot.env, mp2 с образами и новый снимок lab-clean (на ноде слота)
$P slot $id
# 4. Вернуть в работу (на ноде менеджера): enable откатывает слот к снимку, поэтому только после шага 3
$P enable $id
```

- Шаги 2–3 повторяются **для каждого слота**. Шаг `slot` без номеров обработает все слоты ноды, но каждый
  из них должен быть выключен и в `maintenance`. Чтобы разом освободить все слоты стенда, включите режим обслуживания
  `pct exec $MGR -- $ADM maintenance on --in 10m`: лабы остановятся к сроку, и новые не начнутся. Затем переведите
  слоты в `slot-maintenance` и выполните шаги. В конце: `$P enable` и `$ADM maintenance off`.
- Пакеты, нужные GNS3 (QEMU, ubridge, VPCS, Dynamips), не обновляйте без нужды. Сервер GNS3 живёт
  в своём venv `/opt/gns3-server`, закреплён на 2.2.61, и apt его не трогает. `openssh-server` не ставьте:
  слоты обслуживаются через `pct`.
- Если шаг `slot` выдал `WARNING: GNS3 2.2.61 does not answer`, apt сломал GNS3: не включайте слот,
  пока не исправите.
- Образы устройств GNS3 (QEMU, IOU) так не ставят: они лежат в `IMAGES_DIR` на CephFS (см. «Образы GNS3 на CephFS»).

#### Много слотов: новый шаблон и пересоздание слотов

Чтобы не ставить пакет в каждый слот, его ставят один раз в шаблон, а слоты удаляют и создают заново из
нового шаблона с теми же VMID и IP. Секреты, база менеджера и каталоги студентов остаются. **`reset` для этого
не нужен и вреден**: он удаляет менеджер вместе с базой. Записи владельцев у агентов (`/var/lib/lab-node-agent/<vmid>.json`)
не удаляйте: база менеджера жива, её эпохи только растут, и агент примет новый CT с тем же VMID.

Шаблон (`template: 1`) не запускается, а `template` в `deploy` собирает шаблон, только если его VMID ещё нет.
Поэтому новый шаблон делают из клона старого под новым VMID. Новый VMID должен быть вне `SLOTS` и диапазонов слотов.

```sh
# 1. Новый шаблон: клон текущего (2000 — SLOT_TEMPLATE), пакеты, проверка, чистка (на ноде шаблона)
pct clone 2000 2010 --full 1 --hostname lab-slot-template
pct start 2010
pct exec 2010 -- env DEBIAN_FRONTEND=noninteractive apt-get update -qq
pct exec 2010 -- env DEBIAN_FRONTEND=noninteractive apt-get install -y <пакеты>
pct push 2010 /mnt/pve/cfs/gns3-ro/install/slot-template-setup /root/slot-template-setup --perms 0755
pct exec 2010 -- env GNS3_ID=1500 /root/slot-template-setup verify   # GNS3_ID = SLOT_GNS3_ID
pct exec 2010 -- /root/slot-template-setup clean
pct shutdown 2010 && pct template 2010

# 2. secrets/lab.conf: SLOT_TEMPLATE="2010=<нода>:dhcp"; в CREATE_SLOTS — ВСЕ слоты со своими нодами и IP
#    (если слоты создавались вручную, впишите их: create клонирует только то, что там описано)

# 3. Остановить работу и вывести слоты (на ноде менеджера)
pct exec $MGR -- $ADM maintenance on --in 10m
pct exec $MGR -- $ADM maintenance wait
$P check                                          # не должно быть QUARANTINED: такой слот сначала разберите
for id in 2001 2002 2101 2102; do pct exec $MGR -- $ADM slot-maintenance $id --message "новый шаблон"; done

# 4. Удалить слоты (каждый — на своей ноде). --purge убирает CT и из пула lab-slots, его вернёт secrets
pct stop 2001; pct destroy 2001 --purge 1 --destroy-unreferenced-disks 1

# 5. Создать заново и провизить: create, secrets (пул lab-slots), slot на каждой ноде, ..., check, enable
$P deploy
pct exec $MGR -- $ADM maintenance off
```

- Вместо `deploy` можно выполнить только нужные шаги: `$P create` → `$P secrets` → `$P slot` на каждой ноде со слотами →
  `$P check` → `$P enable`. Шаги `agent` и `manager` не нужны: VMID, IP и `slots.json` не изменились. `deploy`
  проходит и их, и `venv slot`: это дольше, но безопасно, пока стенд в обслуживании.
- Старый шаблон 2000 удаляйте (`pct destroy 2000`) только после того, как удалены слоты: со связанными клонами
  (`SLOT_CLONE=linked`) Proxmox его не удалит. Можно и оставить его для отката: верните `SLOT_TEMPLATE` и повторите шаги 3–5.
- Новые MAC слоты получат при клонировании. Если DHCP или firewall привязаны к MAC, обновите их.

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
Разбор кодов (`NO_SLOTS` и другие), добавление слотов на ноду, сборка ZIP плагина — [`TROUBLESHOOTING.md`](TROUBLESHOOTING.md).

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
  GNS3, образы, `/dev/kvm`, читает uid/gid `gns3`, выключает слот и делает его снимок `lab-clean`
  (перед снимком убирает точки агента `mp0`, `mp1`, `mp2`, см. «Снимок чистого слота»).
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

## Выход лабы в интернет (узел «Интернет»)

Задание — `docs/lab-uplink/TASK.md`. Сервер PPPoE (BRAS) ставится отдельно; здесь — всё до него.

Параметры `lab.conf` (все три — на весь кластер):

| Параметр | Что |
|---|---|
| `INET_BRIDGE` | мост Proxmox, на котором лежит общий VLAN выхода |
| `INET_VLAN` | тег этого VLAN (1–4094) |
| `BRAS_MACS` | MAC сервера PPPoE через пробел (нужен только фильтру) |

`INET_BRIDGE` и `INET_VLAN` пустые — всё как раньше: у слотов нет `net1`, в `slot.env` нет `SLOT_UPLINK_MAC`.
Заданы — `template` создаёт у шаблона `net1` (`name=eth1,bridge=…,tag=…,ip=manual`, без `firewall=1`: его `macfilter`
отбросил бы кадры облачной ВМ), `_clone-slot` переписывает `net1` клона без `hwaddr`, а `slot` кладёт в `slot.env`
`SLOT_UPLINK_MAC=02:6c:61:XX:XX:XX`, где `XX:XX:XX` — VMID слота (повторный запуск даёт то же значение).
Шаблон, собранный раньше без `net1`, `_clone-slot` отклоняет с подсказкой `pct set <шаблон> -net1 …`.

Порядок:

```sh
# 1. lab.conf: INET_BRIDGE, INET_VLAN, BRAS_MACS; шаблон со вторым адаптером (новый или pct set …-net1)
# 2. на КАЖДОМ узле со слотами, до первого запуска лаб с интернетом:
/mnt/pve/cfs/gns3-ro/install/lab-uplink-filter --check     # показать правила, ничего не применяя
/mnt/pve/cfs/gns3-ro/install/lab-uplink-filter --install   # применить и включить lab-uplink-filter.service (после перезагрузки)
# 3. слоты: $P slot  (пересоздаёт slot.env с SLOT_UPLINK_MAC), $P snapshot
```

`lab-uplink-filter` (nftables, семейство `bridge`, таблица `lab_uplink`) работает с портами слотов `veth*i1` моста:
от них в VLAN проходит только PPPoE (`0x8863`/`0x8864`) на MAC BRAS или широковещательный (PADI), к ним — только PPPoE
от MAC BRAS, остальное отбрасывается, поэтому слоты друг друга не видят. Физический аплинк узла и порты BRAS не
затрагиваются. При vlan-aware мосте `INET_BRIDGE` — сам мост (фильтр проверяет, что `veth*i1` стоят в `INET_VLAN`
как PVID); иначе — мост VLAN, который создаёт Proxmox (`vmbr1v3000`): передайте его в `--bridge`. Скрипт идемпотентен:
пересоздаёт только свою таблицу. Параметры берутся из `lab.conf` или из `--bridge`, `--vlan`, `--bras-macs`.

Проверка: `nft list table bridge lab_uplink`; `tcpdump -e -i <мост> ether proto 0x8863` — PADI узла «Интернет»; с `eth1`
слота `arping` на другой слот — тишина. Нужен модуль ядра `nft_meta_bridge` (правила используют `meta ibrname`).

## Смотреть базу менеджера: `lab-db`

`lab-db` запускают от root на ноде, где работает LXC менеджера (`MANAGER_VMID` из `lab.conf`). Он заходит
в LXC через `pct exec` под пользователем `lab-manager`, берёт `DATABASE_URL` из `/etc/lab-manager/manager.env`
(секреты не печатает) и открывает базу только на чтение: запись, например `DELETE`, база отклонит.

```sh
L=/mnt/pve/cfs/gns3-ro/install/lab-db
$L sessions 5          # последние сессии: слот, статус, этап, код ошибки
$L slots               # состояние слотов (то же, что lab-manager-admin slots, плюс владелец и in_config)
$L why                 # почему упала последняя сессия с ошибкой: error, ответ слота, события
$L why s-b8f66938cff9bb66
$L calls s-b8f66938cff9bb66     # вызовы слота: deploy/stop, operation_id, result (ответ слота)
$L events s-b8f66938cff9bb66    # журнал событий сессии
$L sql "SELECT status, count(*) FROM sessions GROUP BY 1"
```

В `why` причину сбоя слота ищите в `error.source.error` и в `result` вызова `deploy`: слот пишет туда
свои `code` и `message` (например, нет образа). Слот после сбоя остаётся `failed`, пока оператор не
выполнит `lab-provision enable <VMID>`. Если LXC менеджера на другой ноде, скрипт скажет об этом.

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
