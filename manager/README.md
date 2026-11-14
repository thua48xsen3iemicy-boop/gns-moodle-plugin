# Lab Manager — 0.24.0

0.24.0: модель процессора QEMU для образа (CONTRACTS §2.6, §4.2). Поле `"slot": {"cpu": "…"}` в `IMAGES_FILE` (только `qemu`) —
значение ключа QEMU `-cpu`, например `"host"` или `"SandyBridge,+rdrand,+avx2"`; менеджер кладёт его в узлы образа как
`NodeSpec.cpu`. Без поля слот 0.20.0 сам ставит `-cpu host` (меняется `SLOT_QEMU_CPU` в `slot.env`), поэтому поле нужно, только
если образу нужна другая модель. Вложенная виртуализация (Proxmox или KVM внутри ноды) работает с `host`, если она включена в ядре
узла Proxmox (`cat /sys/module/kvm_intel/parameters/nested` → `Y`, у AMD — `kvm_amd`). Нужен слот 0.20.0 (возможность `qemu-cpu`).
Миграций нет; билеты лаб без поля не меняются.

0.23.0: образы без гостевого агента QEMU (CONTRACTS §2.6, §4.2). Поле образа `"qga": false` в `IMAGES_FILE` (только `qemu`)
говорит, что в госте агента нет и спросить его нельзя: менеджер кладёт в узлы образа `qga: false`, и слот такую ноду
ни о чём не спрашивает — фактов о ней нет (автопроверки пропускают её как `no_collector`), выключить её можно только из
самого гостя. Без поля образ считается образом с агентом, как раньше: ставить `"qga": true` не нужно. С `net_config`,
`random_ifnames`, `role`, `uplink_port` и `slot.provisioning` поле несовместимо (менеджер с таким каталогом не стартует).
Нужен слот 0.19.0 (возможность `no-qga`). Миграций нет; билеты прочих лаб не меняются.

Есть ли в образе агент и серийная консоль, проверяют до записи в каталог на любой машине с QEMU, поверх временного слоя
(`qemu-img create -f qcow2 -b image.qcow2 -F qcow2 /tmp/t.qcow2`): запустить `/tmp/t.qcow2` с `-vnc :1 -serial mon:stdio`
и каналом агента, как у слота (`-chardev socket,id=qga0,path=/tmp/qga.sock,server=on,wait=off -device virtio-serial-pci
-device virtserialport,chardev=qga0,name=org.qemu.guest_agent.0`). Приглашение входа в терминале — серийная консоль есть
(`console_type` можно оставить `telnet`); тишина при живом экране VNC — её нет (`"console_type": "vnc"`). После загрузки
`echo '{"execute":"guest-ping"}' | socat -t 3 - UNIX-CONNECT:/tmp/qga.sock`: ответ `{"return": {}}` — агент есть, тишина —
`"qga": false`. Образ без агента и без серийной консоли ставится только с `"console_type": "vnc"`: у telnet-ноды без агента
слот считает готовность по открытому порту консоли, а студент увидит пустой терминал.

0.22.0: дополнительные диски нод (CONTRACTS §2.6, §4.2). Образ с `extra_disks: true` в каталоге (только `qemu`, не ISP)
даёт в редакторе лаб блок «Дополнительные диски»: до трёх пустых дисков на ноду, 1–20 ГиБ каждый. Нода определения
получает поле `disks: [{"size_gb": N}]`, менеджер переносит его в `NodeSpec.disks` билета. Новое необязательное
ограничение сайта `max_disk_gb` (`sites.json`, `limits`) — сумма дополнительных дисков одной лабы; без него суммы
никто не ограничивает. `GET /info` объявляет возможность `extra_disks`. Нужен слот 0.18.0 и пустой образ
`blank.qcow2` в `/opt/gns3/images/QEMU`: ставьте `extra_disks: true` в `IMAGES_FILE` только после того, как он положен
(`qemu-img create -f qcow2 blank.qcow2 1M`). `lab_def` и билеты лаб без дисков не меняются. Миграций нет.

0.21.3: автопроверки знают сеть ISP (`docs/autochecks/README.md`). Нода с образом, у которого есть `uplink_port`, раздаёт на
порт N сеть `198.18.N.0/24`. Адрес из неё на интерфейсе, который кабелем (напрямую или через коммутаторы) достаёт до
этого порта, больше не `address_not_private`. Шлюз `198.18.N.1` такого хоста — не `gateway_wrong`, а сам интерфейс — не
`member_isolated`. Адрес `198.18.x` в любом другом месте по-прежнему ошибка. Миграций нет.

0.21.2: лаба с `random_ifnames` больше не падает по таймауту `provisioning`. Гость Debian после загрузки ещё просит адрес
по DHCP (`allow-hotplug`, `ifup@ens3`), а в лабе сервера нет. Такой `ifup` держит блокировку интерфейса, и `ifdown`
скрипта переименования его ждал. Потом скрипт снова запускал DHCP на новом имени, и вместе это превышало 120 с команды.
Теперь скрипт сначала останавливает `ifup@<имя>.service` и `dhclient` этого интерфейса. Повторный `ifup` идёт
параллельно и не дольше 20 с, после него `dhclient` снова гасится, чтобы не мешать скрипту сегментов.

0.21.1: шлюз консолей не даёт одной лабе занять весь шлюз (CONTRACTS §6). Открытых консолей не больше 6 на ноду,
40 на проект слота и 400 всего, неиспользованных билетов — не больше 40 на проект; сверх предела браузер получает
`4001`, билет — `429`. Пул соединений aiohttp больше не ограничен сам по себе. Рукопожатие со слотом ограничено 15 с,
соединение со слотом закрывается при любом сбое ответа браузеру. Пределы меняются переменными `GW_MAX_*` и
`GW_HANDSHAKE_TIMEOUT` в `gateway.env` (§1.1). Миграций нет.

0.21.0: случайные имена сетевых интерфейсов гостя (CONTRACTS §2.6). Образ с `random_ifnames: true` в каталоге
(`qemu` с `net_config` `ifupdown`, `networkmanager`, `netplan` или `networkd`) позволяет преподавателю отметить ноду
в редакторе лабы. Такая нода получает при создании проекта команду `provisioning`: интерфейсы переименовываются
по MAC через `/etc/systemd/network/10-gns3lab-<mac>.link` в имена реальных машин (`eno1`, `ens18`, `enp3s0`,
`enp4s0f2`, `enp65s0f1np1`, `enx<MAC>`, `em1`, `p2p1`…). Имена зависят от проекта студента, и при возобновлении
они те же. `GET /info` объявляет возможность `random_ifnames`. Миграций нет.

0.20.0: режим обслуживания (CONTRACTS §2.11). `lab-manager-admin maintenance on --in 5m --message "…"` объявляет
обслуживание: новые запуски отклоняются, пользователи видят предупреждение с обратным отсчётом, а в назначенное время
менеджер штатно останавливает все лабы, включая авторские (проекты сохраняются). `maintenance wait` ждёт, пока ничего
не работает; `maintenance off` возвращает обычную работу. Миграция `013_maintenance.sql`: после обновления —
`admin migrate`.

0.19.1: автопроверка больше не помечает свежие факты как `stale_facts` (CONTRACTS §2.9). Слот ставит `attempted_at`
с точностью до секунды, а `started_at` прогона — с микросекундами: попытка в ту же секунду считалась старой, и ноды
с быстрым сбором (IOL читает файл конфигурации) ждали следующего цикла через 30 с, дольше `CHECK_FACTS_WAIT`. Сравнение
теперь посекундное, а пока факты не свежие, менеджер повторяет просьбу собрать их раз в 6 с: первую слот мог отбросить
по `SLOT_FACTS_MIN_REFRESH`.

0.19.0: удаление наработок (CONTRACTS §2.10). `POST /api/v1/purges` удаляет workspace активности, пользователя, лабы
библиотеки или одного студента в активности («Wipe»): живые сессии останавливаются, workspace с сессиями, фактами и
прогонами уходит из БД, каталог проекта на CephFS удаляет агент узла 1.4.0 (`DELETE /students/{key}/projects/{id}`) из
очереди `project_purges` с повторами. `POST /api/v1/purges/reconcile` — суточная сверка со списком активностей Moodle.
Запуск в удаляемый workspace — `409 workspace_purging`. Миграция 012 (`admin migrate`) заодно ставит в очередь авторские
проекты, брошенные при смене определения.

0.18.2: «Стоп» во время запуска больше не «мигает». Остановку менеджер выполняет между шагами, а шаг `lab_wait` длится,
пока грузятся устройства; раньше до конца шага опрос отдавал `starting`, и кнопка «Стоп» возвращалась после каждого
обновления. Теперь запрошенная остановка во всех ответах сразу `stopping` с этапом `stop_pending` (CONTRACTS §2.3).

Автопроверки, этап 3 (`../docs/autochecks/README.md`): правила, которым были нужны данные слота 0.17.0, теперь работают —
имя узла (`hostname_wrong`, у Linux, Windows, RouterOS и Cisco), пересылка пакетов на маршрутизаторе и хосте
(`forwarding_off`, `forwarding_on_host`), сохранённая настройка сети Linux против живой (`address_not_saved`,
`config_not_applied`, режим `persistence`), связность по таблицам маршрутов и через Cisco IOL (`routes` из `ios.config`),
и выключенная пересылка на Linux-маршрутизаторе по пути. Со слотом старше 0.17.0 эти правила получают `skipped`.

## 0.17.0

Автопроверки, этап 1 (`../docs/autochecks/README.md`, `docs/CONTRACTS.md` §2.9): `POST/GET /api/v1/sessions/{id}/checks`,
возможность `checks` в `info` и в сессии с фактами. Прогон просит слот собрать факты, ждёт свежих до `CHECK_FACTS_WAIT` и
считает правила над снимком: модель L1/L2/L3 (`lab_manager/netmodel.py`, как слои плагина), ожидания из сегментов
`gns3lab.lab/2` и групп узлов, первопричины и оценку по профилю строгости (`lab_manager/checks.py`). Результат — в таблице
`check_runs` (миграция `011_check_runs.sql`: после обновления — `lab-manager-admin migrate`). Описание проверок — поле
`checks` определения `lab/2` и `meta.json` эталонной лабы; сегмент с `configure: "student"` не преднастраивается, он только
задаёт ожидание. Cisco IOL без сохранённой конфигурации проверяется так, как загрузится (находка `config_not_saved`).

Образ с `net_config: "routeros"` получает в каталоге (`GET /api/v1/images`) поле `guest: "routeros"`; менеджер переносит его в
ноды эталонной топологии лабы и в `session.topology` по имени ноды, как `symbol`. По нему плагин 0.18.2 показывает у ноды
MikroTik подсказки консоли RouterOS (`docs/CONTRACTS.md` §2.3, §2.6).

Скрипт RouterOS сначала ждёт до 90 с, пока у CHR появится столько портов `ethernet`, сколько нужно настраиваемым адаптерам:
слот запускает преднастройку при первом ответе агента, а RouterOS может ещё поднимать порты. Нужен слот 0.15.1, у которого
ошибка преднастройки содержит строку гостя.

MikroTik RouterOS (CHR): в каталоге образов `net_config: "routeros"` — преднастройка адресов сегментов скриптом RouterOS
вместо `/bin/sh` (`lab_manager/netconfig.py`). Скрипт находит порт по MAC, как у Linux (адаптер N — N-й `ethernet` по
возрастанию MAC), убирает свои прежние адреса и маршрут (`comment=gns3lab`), снимает DHCP-клиент с настраиваемого порта,
задаёт адрес и маршрут по умолчанию. Нужен слот 0.15.0 (`routeros`): он узнаёт RouterOS по агенту и исполняет команду как
скрипт RouterOS. Факты таких нод — коллектор слота `routeros.net/1`, менеджер их не различает. Образец записи —
`mikrotik-chr7` в `examples/images.json` (`docs/CONTRACTS.md` §2.6, §4.2, §4.6).

Узел «Интернет»: `uplink_port` узла лабы (`gns3lab.lab/*` и эталонная `ticket.json`) идёт в `NodeSpec` слота, лаба с ним
запускается только на слотах с `"features": ["uplink"]` в `slots.json` (`slots.json.example`), иначе `409 unsupported_lab`.
Новые коды проверки лабы — `too_many_uplinks`, `uplink_port_in_use` (`docs/CONTRACTS.md` §2.5, §2.6). В `examples/images.json` —
образ `lab-uplink` с `console_type: none`.

Слот 0.12.0 публикует `iol.l1/1`: менеджер сохраняет его обычным журналом фактов,
оставляет конфигурационные `interfaces` и добавляет `l1_ports` с независимым
`observed_at`/`ttl`/`ok`. Ошибка L1 не помечает ошибкой всю конфигурацию Cisco.
Для сессии с L1 опрос слота идёт с интервалом не больше 3 с, без новых гостевых команд.

Изменения протокола, порядок обновления и стендовая приёмка:
[`docs/pilot-hardening/README.md`](../docs/pilot-hardening/README.md).
Менеджер требует Agent 1.2 (откат слота к снимку) и Slot 0.5 с сохраняемым fencing; старые
исполнители автоматически не допускаются. Размер CT задаёт агент, токен PVE — только чтение.

Сервис между Moodle (`plugin/`) и слотами Lab Slot (`slot/`). Принимает запросы плагина по
контракту `plugin/docs/SPEC.md` §3, подбирает для лабы свободный слот на узле, где хватает
памяти, через агента на узле Proxmox (Lab Node Agent, `agent/` в ветке
`feature/proxmox-node-agent`, mTLS) монтирует каталог студента и запускает LXC, запускает и останавливает лабу в Lab Slot, выдаёт билеты шлюзу консолей.
Слоты не привязаны к студентам. Как всё устроено и почему — в [`docs/DESIGN.md`](docs/DESIGN.md),
там же открытые вопросы.
Форматы данных между плагином, менеджером, каталогом лаб и слотом и правила их расширения —
[`docs/CONTRACTS.md`](docs/CONTRACTS.md); запросы к агенту узла — [`docs/AGENT_REQUESTS.md`](docs/AGENT_REQUESTS.md).

Стек: Python 3.12, FastAPI, Uvicorn, httpx, pydantic, psycopg 3, PostgreSQL (локально в LXC менеджера).

## Размещение

| Что | Где |
|---|---|
| Код и venv | `/srv/gns3-ro/manager` (RO CephFS), venv собирается по этому же пути |
| Конфиг и секреты | `/etc/lab-manager/` (`manager.env`, `sites.json`, `slots.json`, `pve-root-ca.pem`, `node-server-ca.crt`, `manager.crt`, `manager.key`) — не в Git и не на CephFS |
| PostgreSQL | локально в этом LXC, данные — `/var/lib/postgresql` на rootfs (RBD), не на CephFS |
| Каталог лаб | `/srv/gns3-ro/labs/<lab_ref>/` (RO CephFS) |
| Шлюз консолей | в **этом же LXC**, код менеджера (решение владельца): `gateway/console_gateway.py`, отдельный процесс, один на все слоты (§1.1) |

Обновление кода: остановить сервис → заменить `/srv/gns3-ro/manager` → `admin migrate`, если
есть новые миграции → запустить сервис.

## 1. LXC менеджера (на узле PVE, root)

Непривилегированный CT с Ubuntu 24.04. Раскладка CephFS (`LAB_CEPH_ROOT=/mnt/pve/cfs`):
`gns3-ro/` — git checkout репозитория (`agent/ manager/ plugin/ slot/ labs/`, venv в `manager/venv`
и `slot/venv`, не в git), `gns3-rw/students/` — каталоги студентов. Менеджеру администратор
монтирует только его код и лабы, RO:

```sh
pct set <MGR_VMID> -mp0 /mnt/pve/cfs/gns3-ro/manager,mp=/srv/gns3-ro/manager,ro=1 \
                   -mp1 /mnt/pve/cfs/gns3-ro/labs,mp=/srv/gns3-ro/labs,ro=1
```

Каталоги студентов (`gns3-rw`) менеджеру не нужны: монтирует их агент, а создаёт сторона Proxmox (п. 2).
Нужен сетевой доступ к API Proxmox (8006), к агентам узлов (8443, firewall агента пускает
только менеджер) и к слотам (8080): к REST слота ходит менеджер, к консолям — шлюз.

### 1.1. Шлюз консолей в этом LXC

Браузеры подключают консоли к шлюзу, а не к менеджеру; сам менеджер наружу не открыт (к нему ходит
только сервер Moodle). Шлюз — отдельный процесс в том же LXC. Код, тест, unit и nginx — в
`gateway/` (перешёл от плагина, шлюз 0.4.5). Договорённости с браузерной частью плагина (адрес,
билет, кадры, коды закрытия 4000/4001) — `plugin/docs/MANAGER_REQUESTS.md` §12; менять их только
через `docs/PLUGIN_REQUESTS.md`.

```
браузер ──wss──> nginx :443  /console/  ──ws──> шлюз 127.0.0.1:8080 ──ws──> слот :8080 /v1/console/…
сервер Moodle ──https──> nginx ──> менеджер 127.0.0.1:8090 ──http──> шлюз 127.0.0.1:8080/internal/tickets
```

- Шлюз слушает только `127.0.0.1` (`GW_HOST=127.0.0.1`, `GW_PORT=8080`). Наружу nginx публикует
  **только** `/console/` (с `Upgrade`/`Connection` для WebSocket и `proxy_read_timeout` больше 25 с:
  шлюз шлёт ping каждые 25 с). `/internal/tickets` наружу не публикуется.
- Менеджер: `GATEWAY_INTERNAL_URL=http://127.0.0.1:8080`, `GATEWAY_INTERNAL_KEY` = `GW_INTERNAL_KEY`
  шлюза, `GATEWAY_PUBLIC_URL=wss://<публичное имя>` (без `/` на конце).
- Шлюз: переменные `GW_*` не пишутся руками, их генерирует менеджер из своего конфига
  (`GW_SLOTS` — адреса и `console_token` из `slots.json`, `GW_ALLOWED_ORIGINS` — origin сайтов из
  `sites.json`, `GW_INTERNAL_KEY` = `GATEWAY_INTERNAL_KEY`, порт — из `GATEWAY_INTERNAL_URL`,
  `GW_SLOT_CA_B64` — CA слотов из `SLOT_CA_FILE`, целиком одной строкой base64: unit закрывает
  `/etc/lab-manager` от шлюза, и путь к файлу оттуда он не прочитал бы).
  После любого изменения `slots.json`, `sites.json` или CA слотов файл нужно сгенерировать заново и перезапустить шлюз.
- Шлюз работает под своим временным пользователем (`DynamicUser`), секретов менеджера (токен
  Proxmox, API-токены слотов) он не видит.
- Шлюз один на все слоты: билеты живут в его памяти, и менеджер рассчитан на один шлюз.
- Пределы шлюза (0.21.1): `GW_MAX_PER_NODE` (6), `GW_MAX_PER_PROJECT` (40), `GW_MAX_CONSOLES` (400) открытых консолей,
  `GW_MAX_TICKETS_PER_PROJECT` (40) неиспользованных билетов, `GW_HANDSHAKE_TIMEOUT` (15 с) на рукопожатие со слотом.
  `gateway-env` их не пишет: нужны другие значения — добавьте строки в `gateway.env` после генерации. Консоль держит два
  сокета, поэтому `GW_MAX_CONSOLES` выше ~450 требует поднять `LimitNOFILE` в unit шлюза.

Установка (после §3):

```sh
cd /srv/gns3-ro/manager
venv/bin/python -m pip install -r gateway/requirements.txt          # aiohttp, в тот же venv
sudo -u lab-manager ./lab-manager-admin gateway-env | install -m 600 /dev/stdin /etc/lab-manager/gateway.env
cp gateway/lab-console-gateway.service /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now lab-console-gateway
# nginx: блок из gateway/nginx-console.conf внутрь server {} с TLS для имени из GATEWAY_PUBLIC_URL
```

Проверка без слотов: `venv/bin/python gateway/tests/check_gateway.py` → `ALL OK` (поддельный слот).

#### VNC-консоль (менеджер 0.9.0)

Нода из образа каталога с `console_type: "vnc"` (CONTRACTS §2.6) показывает студенту экран, а не терминал.

- **Слот.** Менеджер кладёт `console: "vnc"` в `NodeSpec` только таких нод, в `nodes[]` обычного `deploy/1`
  (CONTRACTS §4.2). У остальных нод поля нет, поэтому канонический JSON и `lab_revision` существующих лаб
  не меняются. `deploy/2` и `requires` в менеджере пока нет, возможности слота он не читает: слот до 0.8.0
  молча отбросил бы поле, поэтому менеджер и слот выпускаются вместе (слот 0.8.0 и новее).
- **Билет консоли.** `POST /api/v1/sessions/{id}/console-tickets` отдаёт тот же ответ и тот же `gateway_url`.
  Тип консоли менеджер берёт из живых статусов нод (`session.nodes[].console_type`). Для `vnc`-ноды
  билет с `readonly: true` — `403 console_disabled` («экран виден только владельцу сессии»; проверка идёт перед
  `409 node_not_running`, но после `console_disabled` для образов без консоли); билет владельца уходит шлюзу
  с `"console": "vnc"`. Для telnet-нод шлюзу `console` не передаётся (по умолчанию `telnet`).
- **Шлюз.** Браузер подключается так же: `wss://<шлюз>/console/<node_id>?ticket=…`, но по соединению идёт
  сырой RFB: двоичные кадры в обе стороны без разбора, текстовые кадры браузера отбрасываются. Origin, билет и
  коды закрытия `4000`/`4001` те же. Подробнее — шапка `gateway/console_gateway.py`.

## 2. Сторона Proxmox: агент, слоты, каталоги

1. Агент на каждый узел — по `agent/README.md` (ветка `feature/proxmox-node-agent`):
   `make-demo-pki.sh` (или своя PKI), `install-node.sh`, `LAB_VMID_MIN/MAX`, firewall.
   Менеджеру из PKI нужны `manager.crt`, `manager.key` и `node-server-ca.crt`.
   Агент монтирует в слот `<LAB_CEPH_ROOT>/<LAB_CODE_DIR>` (код, `gns3-ro/slot`) в `/srv/gns3-ro/slot`
   и `<LAB_CEPH_ROOT>/<LAB_STUDENTS_DIR>/<key>` (`gns3-rw/students/<key>`) в `/opt/gns3/projects`.
   Все запросы к агенту — в [`docs/AGENT_REQUESTS.md`](docs/AGENT_REQUESTS.md).
2. Слоты: CT из диапазона `LAB_VMID_MIN..MAX`, `unprivileged: 1`, `pct set <vmid> --tags labslot`,
   внутри — Lab Slot по `slot/README.md` и `/usr/local/sbin/lab-slot-init` из `agent/guest/`.
3. Каталоги на CephFS (агент требует, чтобы предки принадлежали root и не были доступны на
   запись группе и остальным):

   ```sh
   CEPH=/mnt/pve/cfs
   install -d -o root -g root -m 0755 $CEPH/gns3-rw $CEPH/gns3-rw/students $CEPH/gns3-rw/students/placeholder
   ```

   Каталоги студентов `gns3-rw/students/<slug сайта>-<user_id>` создаёт агент по запросу менеджера
   перед каждым стартом. Для этого на каждом узле задаётся владелец — `gns3` слота на хосте
   (uid/gid в слоте + 100000, см. `agent/README.md`, «Student directory provisioning»):

   ```sh
   systemctl edit lab-node-agent    # Environment=LAB_STUDENT_HOST_UID=101000 / LAB_STUDENT_HOST_GID=101000
   ```

   Без этой настройки агент отвечает 503, и сессии ждут на этапе `student_dir`.

## 3. Установка в LXC менеджера

```sh
apt install -y python3.12-venv postgresql
useradd --system --home-dir /nonexistent --shell /usr/sbin/nologin lab-manager

# PostgreSQL: роль = системный пользователь (peer-аутентификация через сокет, без пароля).
sudo -u postgres createuser lab-manager
sudo -u postgres createdb -O lab-manager lab_manager

# Код (на RW-стороне CephFS, с тем же абсолютным путём):
cd /srv/gns3-ro/manager
python3.12 -m venv venv
venv/bin/python -m pip install -r requirements.txt

# Конфиг:
install -d -o root -g lab-manager -m 0750 /etc/lab-manager
cp /srv/gns3-ro/manager/manager.env.example /etc/lab-manager/manager.env
cp /srv/gns3-ro/manager/sites.json.example  /etc/lab-manager/sites.json
cp /srv/gns3-ro/manager/slots.json.example  /etc/lab-manager/slots.json
cp /etc/pve/pve-root-ca.pem /etc/lab-manager/        # скопировать с узла PVE
# mTLS к агентам: CA агентов, клиентский сертификат и ключ менеджера.
cp node-server-ca.crt manager.crt manager.key /etc/lab-manager/   # из pki/ после make-demo-pki.sh
chown lab-manager:lab-manager /etc/lab-manager/sites.json /etc/lab-manager/slots.json /etc/lab-manager/manager.key
chmod 600 /etc/lab-manager/sites.json /etc/lab-manager/slots.json /etc/lab-manager/manager.key
chown root:lab-manager /etc/lab-manager/manager.env && chmod 640 /etc/lab-manager/manager.env
# Отредактировать: секреты (openssl rand -hex 32), адреса слотов и их токены, PVE_*, AGENT_*, GATEWAY_*.

# Схема БД (и после каждого обновления кода с новыми миграциями):
sudo -u lab-manager /srv/gns3-ro/manager/lab-manager-admin migrate

cp /srv/gns3-ro/manager/lab-manager.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now lab-manager.service
```

Unit: `ExecStartPre` — preflight (конфиг, права 0600 на файлы секретов, CA Proxmox, сертификаты
mTLS, каталог лаб, версия схемы). Без `manager.env`, секретов или с неверной схемой сервис не
стартует (fail closed). `run.py` проверяет, что код и venv лежат в `/srv/gns3-ro/manager`.
Uvicorn работает в один процесс: оркестратор живёт в нём же.

Готовность: `curl -s 127.0.0.1:8090/readyz` (200 `ready` / 503 `not_ready` с причинами).

## 4. Конфигурация

`manager.env` (полный список — в `manager.env.example`):

| Переменная | Смысл |
|---|---|
| `DATABASE_URL` | `postgresql:///lab_manager` (сокет, peer) |
| `MANAGER_SITES_FILE` | сайты Moodle: `url` (= `$CFG->wwwroot`), `slug` (часть `student_key`; **после появления данных не меняется**: новый slug — это новые каталоги студентов), `secret` (Bearer), необязательные `limits` для лаб из Moodle: `max_nodes` (≤ 100), `max_memory_mb`, `max_cpus`, `images` (список `id` или `"*"`) |
| `MANAGER_SLOTS_FILE` | слоты: `vmid`, `address` (= `SLOT_LISTEN_HOST` слота и ключ в `GW_SLOTS` шлюза), `port`, `api_token`, `console_token`. Токены у всех слотов разные (каждый клон CT со своим `slot.env`), иначе менеджер не стартует |
| `LABS_DIR` | каталог лаб; `meta.json` может задавать `resources: {cores, memory_mb}` |
| `IMAGES_FILE` | каталог образов для лаб, которые преподаватели собирают в Moodle (пример — `examples/images.json`). Не задан — лабы только из `LABS_DIR`. Формат и проверки — `lab_manager/images.py`. Образ с `"console_type": "none"` (Linux-коммутатор) не получает билет консоли ни у кого, и у студента, и у наблюдателя (`403 console_disabled`, CONTRACTS §2.4); правило действует и для эталонных лаб с тем же файлом образа, значение читается при старте менеджера. Поле образа `"net_config"` (`ifupdown`, `networkmanager`, `netplan`, `networkd`, `routeros` — MikroTik CHR; только `qemu`) — механизм сети гостя: по нему менеджер строит преднастройку адресов для сегментов лабы `gns3lab.lab/2` (CONTRACTS §2.6); без него образ (кроме VPCS) в сегмент не ставится. Механизм образа администратор узнаёт в госте: `systemctl is-active NetworkManager systemd-networkd`, `ls /etc/netplan /etc/network/interfaces.d`; образ с netplan поверх NetworkManager (Ubuntu Desktop) — `networkmanager`, `netplan` требует активный `systemd-networkd` (иначе лаба `FAILED` с `net_config mismatch: declared netplan, found …`). Поле ставится в каталоге стенда `secrets/manager/images.json`, а не в `manager/examples/images.json`; после правки — шаг `manager` в `lab-provision`. При любом механизме скрипт выключает сетевую часть cloud-init гостя (`/etc/cloud/cloud.cfg.d/99-gns3lab-network.cfg`), чтобы настройка пережила перезагрузку. Образы IOL настраиваются сами, без поля в каталоге: коммутатор (`kind: "iou"`, `category: "switch"`) на пути сегмента и маршрутизатор (`iou`, `category: "router"`) — шлюз сегмента получают `nvram_config` (CONTRACTS §2.6, §4.2); нужен слот с `iol-nvram`, имя образа не важно, важны `kind` и `category`. Образ с `"console_type": "vnc"` (только `kind: "qemu"`, иначе менеджер не стартует) — нода с экраном QEMU VNC вместо серийной консоли: менеджер кладёт в её узел `console: "vnc"` (см. «VNC-консоль» ниже) |
| `LAB_MEMORY_OVERHEAD_MB` | запас памяти CT для лабы из Moodle: память нод + 10 % + это значение (по умолчанию 1024) |
| `QUEUE_TIMEOUT` | сколько секунд сессия ждёт слот, потом `failed` с `QUEUE_TIMEOUT`; `0` — без ограничения (по умолчанию 600) |
| `SLOT_LAB_FAILURE_LIMIT` | слот, на котором подряд упали лабы стольких **разных** студентов (без чистой сессии между ними), уходит в `maintenance`; `0` — не выводить (по умолчанию 3) |
| `PVE_NODES`, `PVE_TOKEN_ID`, `PVE_TOKEN_SECRET`, `PVE_CA_FILE` | точки входа API, токен, CA кластера |
| `AGENT_URL_TEMPLATE` | `https://{node}.lab.local:8443`; `{node}` — имя узла Proxmox, хост — как в SAN сертификата агента |
| `AGENT_CA_FILE`, `AGENT_CERT_FILE`, `AGENT_KEY_FILE` | `node-server-ca.crt`, `manager.crt`, `manager.key` |
| `LAB_CEPH_ROOT`, `LAB_CODE_DIR`, `LAB_STUDENTS_DIR` | те же, что у агентов (`/mnt/pve/cfs`, `gns3-ro/slot`, `gns3-rw/students`): по ним менеджер точно сверяет источники `mp0`/`mp1` |
| `AGENT_START_TIMEOUT`, `AGENT_MOUNTS_TIMEOUT`, `AGENT_STOP_TIMEOUT` | 720/120/130 с — больше сумм таймаутов внутри агента. `AGENT_START_TIMEOUT` не меньше 540 с: агент 1.2.1 тратит на запуск до 120 с на `pct start` и по `LAB_GUEST_ACTION_TIMEOUT` (120 с, до 600 с) на каждое из трёх действий, плюс запас 60 с. Агент сообщает этот бюджет в `GET /capabilities` (`start_budget_seconds`); если он больше настройки, менеджер пишет предупреждение в журнал при старте, то же показывает `lab-manager-admin check`. Прежнее значение 420 с с агентом 1.2.1 не проходит проверку: уберите настройку или поставьте не меньше 540 |
| `AGENT_START_ACTIONS` | действия в госте после `pct start`; по умолчанию `init_slot` (`agent/guest/lab-slot-init`) |
| `SLOT_CODE_REVISION` | пусто: общий `<ceph>/<LAB_CODE_DIR>`; иначе `<ceph>/<LAB_CODE_DIR>/<revision>` |
| `PLACEHOLDER_STUDENT_KEY` | обязательный каталог-заглушка RO (`placeholder`); пустое значение запрещено |
| `NODE_MEMORY_RESERVE_MB` | сколько памяти оставлять свободной на каждом узле при размещении (по текущему использованию) |
| `NODE_GUEST_MEMORY_MB` | предел суммы лимитов памяти гостей узла, MiB; остальное — хосту и Ceph (0 — без предела) |
| `NODE_CPU_OVERCOMMIT` | предел vCPU гостей узла на один CPU узла, например `2` (0 — без предела) |
| `GATEWAY_INTERNAL_URL`, `GATEWAY_INTERNAL_KEY`, `GATEWAY_PUBLIC_URL` | `/internal/tickets` шлюза, его `GW_INTERNAL_KEY`, `wss://` для браузера |
| `DEFAULT_MAX_DURATION`, `MAX_DURATION_LIMIT` | длительность сессии по умолчанию и предел для `options.max_duration` от плагина, минуты |
| `FACTS_POLL_INTERVAL`, `FACTS_TTL` | как часто читать факты лабы у слота 0.6.0+ (15 с) и `ttl` снимка для плагина (90 с = три периода сбора слота). История хранится в БД: `docs/DESIGN.md` §6.7 |
| `CHECK_FACTS_WAIT`, `CHECK_TIMEOUT` | сколько прогон автопроверки ждёт свежих фактов всех нод (20 с) и через сколько незавершённый прогон получает `failed` с `TIMEOUT` (120 с), `docs/CONTRACTS.md` §2.9 |

## 5. Токен Proxmox

Запуском, остановкой и монтированием занимается агент, поэтому токен — обычный пользователь
(не root) с правами только на пул слотов и на узлы:

```sh
pveum role add LabManager -privs "VM.Audit"
pveum role add LabManagerNodes -privs "Sys.Audit"
pveum user add lab-manager@pve
pveum pool add lab-slots
pveum pool modify lab-slots -vms 2001,2002,2003
pveum user token add lab-manager@pve manager --privsep 1     # секрет показывается один раз
# Токен с --privsep 1 получает пересечение своих прав и прав пользователя: роли нужны обоим.
pveum acl modify /pool/lab-slots -user lab-manager@pve -role LabManager
pveum acl modify /nodes -user lab-manager@pve -role LabManagerNodes
pveum acl modify /pool/lab-slots -token 'lab-manager@pve!manager' -role LabManager
pveum acl modify /nodes -token 'lab-manager@pve!manager' -role LabManagerNodes
```

- `VM.Audit` — `/cluster/resources` (CT), статус и конфиг CT (проверка `mp1`);
- размер CT задаёт агент с проверкой владельца; прав VM.Config.CPU/Memory менеджеру не требуется;
- `Sys.Audit` на `/nodes` — узлы в `/cluster/resources` (загрузка памяти для размещения).

Почему bind mount не делается через токен: см. DESIGN §3. Проверить на своей версии PVE
(ожидается 403 «mount point type bind is only allowed for root@pam»):

```sh
curl -s --cacert /etc/lab-manager/pve-root-ca.pem -X PUT \
  -H "Authorization: PVEAPIToken=root@pam!test=SECRET" \
  --data-urlencode 'mp9=/tmp,mp=/mnt/test' https://pve1:8006/api2/json/nodes/pve1/lxc/<STOPPED_TEST_CT>/config
```

## 6. Команды оператора

```sh
ADM="sudo -u lab-manager /srv/gns3-ro/manager/lab-manager-admin"
$ADM check-config        # сводка без секретов
$ADM check               # слоты в карантине и бюджет запуска агентов (см. ниже)
$ADM labs                # проверка каталога лаб (lab.gns3 ↔ ticket.json)
$ADM slots               # реестр слотов
$ADM slot-enable 2001    # maintenance/failed → free (CT выключен, откат к снимку, заглушка)
$ADM slot-maintenance 2001
$ADM maintenance on --in 5m --message "Обновление образов"   # режим обслуживания (ниже)
$ADM maintenance status  # фаза и что ещё работает
$ADM maintenance wait --timeout 1800   # ждать фазы active; код 1 по тайм-ауту
$ADM maintenance off
```

**Режим обслуживания** (CONTRACTS §2.11) — для работ, при которых лабы должны быть остановлены. `on --in <задержка>`
(`5m`, `90s`, `1h`, `0` — сразу; число без единицы — минуты) объявляет его: новые запуски отклоняются, сессии в очереди
отменяются, у всех запущенных лаб, в том числе в редакторе, плагин показывает предупреждение и обратный отсчёт. В
назначенное время менеджер штатно останавливает каждую лабу (`stop_reason: maintenance`), проекты сохраняются.
Снятие образов, начатое до этого, доводится до конца. Когда `status` показывает `"ready": true` (фаза `active`), ничего
не работает: можно обновлять слоты, агенты, образы или сам менеджер (состояние режима хранится в БД и переживает
перезапуск). Очередь удаления каталогов (§2.10) на это время стоит. `off` возвращает обычную работу; слоты включать
не нужно. Повторное `on` переносит время остановки и меняет текст.

**Карантин слота.** Если агент ставит на слот отметку `uncertain` (команда `pct` не уложилась в тайм-аут,
`COMMAND_UNCERTAIN`/`PCT_TIMEOUT`), он отклоняет любые изменения и `claim`, пока оператор не сверит слот. Ждать
нечего, и очистка через агента невозможна, поэтому менеджер **сразу** завершает сессию кодом `SLOT_QUARANTINED`
(`retryable: false`), не пытаясь остановить CT. Слот уходит в `maintenance` (`state_message` начинается с
`SLOT_QUARANTINED`), `student_key` остаётся: CT может работать с каталогом студента на запись. Повторный запуск
того же студента до сверки получает `STUDENT_DIR_BUSY` с тем же текстом («Слот требует проверки администратором…»);
другие студенты работают на других слотах. В журнале сессии — событие `slot_quarantined` (`vmid`, `node`,
`command`, `stage`). `$ADM check` (и `lab-provision check`) перечисляет такие слоты: VMID, сессию, студента, команду
из отметки агента и шаги `pct shutdown` → `lab-provision reconcile <VMID>` → `lab-provision enable <VMID>`.
После сверки ключ студента снимается обычным путём, и лаба запускается снова, проект продолжается.
Сетевые сбои и `503` агента карантином не считаются: менеджер ждёт и перечитывает состояние.

Новый слот из `slots.json` появляется в состоянии `maintenance`, пока оператор его не включит.
Слот живёт одну сессию. При освобождении, в том числе после сбоя, менеджер останавливает лабу
(если она могла появиться) и CT, агент откатывает CT к снимку чистого слота (`lab-clean`, шаг
`lab-provision snapshot`), и только потом менеджер монтирует заглушку (DESIGN §6.4). Что студент
сделал в госте, до следующей сессии не доходит, а его каталог не остаётся подключённым.

После этого слот снова `free`, если виновата лаба: `SLOT_LAB_FAILED`, `SLOT_OPERATION_FAILED`,
`SLOT_PROJECT_UNKNOWN`, `SLOT_STOP_FAILED`, `LAB_UNAVAILABLE`, таймаут на этапах лабы. Оператор
для этого не нужен: беда остаётся у студента, в его каталоге.

Слот остаётся `failed`, если виноват он сам (CT не запускается, агент или Proxmox отказали, не те
монтирования, слот не стал готов): чистый гость сломался бы так же. **Такой слот не откатывается**:
CT остановлен и запаркован (каталог студента отключён), а гость остаётся как был, чтобы оператор мог
прочитать журнал слота (`/var/lib/lab-slot`, `GET /v1/operations/…`), логи GNS3 и journald гостя.
То же для `maintenance` после сбоя владения слотом (`SLOT_OWNER_LOST`, `SLOT_NODE_CHANGED`).
Оператор разбирается и включает слот (`slot-enable`): только тогда он откатывается к снимку.
Студент тем временем может запустить лабу на любом другом слоте.

Слот уходит в `maintenance` и в двух других случаях. Не подтверждены откат или заглушка (нет
снимка, агент старше 1.2, ошибка `pct`): в этом случае он уже запаркован. Или подряд упали лабы
стольких разных студентов, сколько задано в `SLOT_LAB_FAILURE_LIMIT` (по умолчанию 3): так выглядит
сломанный слот, на котором не идёт ни одна лаба. Сбои, которые считаются, — `SLOT_LAB_FAILED`,
`SLOT_OPERATION_FAILED`, `SLOT_STOP_FAILED`, таймаут на этапах лабы; один и тот же студент
считается один раз, `LAB_UNAVAILABLE` и `SLOT_PROJECT_UNKNOWN` не считаются совсем. Лаба, которая
запустилась и штатно остановилась, обнуляет счёт; `slot-enable` тоже. `slots` показывает его в
`lab_failures`. Слот в таком `maintenance` откатан, смотреть в нём нечего, кроме причины: ищите её
по `events` и журналам упавших сессий.

**Обновление с 0.6.** Сначала Agent 1.2 на всех узлах, затем `lab-provision snapshot` для каждого
слота (слот выключен и в `maintenance`; новые слоты получают снимок в шаге `slot`), и только потом
менеджер 0.7. Без снимка каждый слот после первой же сессии уйдёт в `maintenance`
(`reset failed: SNAPSHOT_MISSING`).

Если CT остановить не удалось, `slots` показывает у слота `student_key`. Пока оператор не
выключит CT и не сделает `slot-enable`, этот студент получает `STUDENT_DIR_BUSY`: его каталог не
монтируется во второй слот.

**Нужен Lab Slot 0.4.0 или новее.** Следующая сессия студента всегда продолжает его проект
(DESIGN §6.1), в каком бы слоте она ни запустилась; настройки `LAB_PERSISTENCE` больше нет (если она
осталась в `manager.env` со значением, отличным от `resume`, менеджер не стартует). Проекты, которые
создал слот 0.3 в старом режиме `fresh` (по проекту на сессию), продолжаться не будут: это просто
старые каталоги в `gns3-rw/students/<key>/`, их можно удалить вручную.

При обновлении до схемы 3 (`admin migrate`) у каждого слота не в `free` ставится `student_key`
его последней сессии: про такие слоты менеджер не знает, выключен ли CT. Проверьте их и сделайте
`slot-enable`, иначе их последние студенты будут получать `STUDENT_DIR_BUSY`.

## 7. Тесты

```sh
cd manager
python3 -m venv /tmp/lm-venv && /tmp/lm-venv/bin/pip install -r requirements.txt pytest
/tmp/lm-venv/bin/python -m pytest -q tests                      # без БД: тесты PostgreSQL пропускаются
DATABASE_URL=postgresql:///lab_manager_test /tmp/lm-venv/bin/python -m pytest -q -rs tests
```

**Внимание:** `DATABASE_URL` для тестов — только одноразовая база. Тесты пересоздают её схему `public`.

Proxmox, агент узла, Lab Slot и шлюз подменяются через `httpx.MockTransport` (`tests/fakes.py`).
Ответы на ревью разработчика агента и что по ним исправлено — [`docs/AGENT_REVIEW_ANSWERS.md`](docs/AGENT_REVIEW_ANSWERS.md).
Ответы разработчику плагина — [`docs/PLUGIN_REQUESTS.md`](docs/PLUGIN_REQUESTS.md).
Ответы разработчику слота (на `slot/docs/ALIGNMENT.md`) — [`docs/SLOT_REQUESTS.md`](docs/SLOT_REQUESTS.md).
Что проверено тестами:
- перебор нод Proxmox: первая не отвечает → вторая; последняя рабочая — первой; 4xx и 500 не
  переключают; 503 на GET переключает; изменяющий запрос с неизвестным исходом не повторяется;
  нода возвращается после `PVE_RETRY_AFTER`; все ноды недоступны; миграция CT;
- UPID: ожидание `stopped`/`OK`, ошибка `exitstatus`, недоступный узел задачи и таймаут → «неизвестно»;
- агент: формат запросов и таймауты, разбор всех ошибок `agent.py` (403/404/409×3/422/503/504)
  и кодов ошибок из `AGENT_REQUESTS.md` §2;
  клиент вручную прогнан против настоящего `agent.py` из ветки (с подменённым `pct`);
- точная сверка `mp0`/`mp1` (чужой root, неверная цель, RO/RW); неудачная парковка → `maintenance`;
  резерв памяти запущенного, но ещё не развернувшегося слота;
- создание каталога студента через агента (`PUT /students/<key>`): порядок, ожидание при 409/503,
  тайм-аут без порчи слота; клиент прогнан против настоящего `ensure_student` на временной ФС;
- порядок `stop (если CT запущен) → cores/memory → агент start (mounts + pct start) → readyz → deploy`
  и `slot stop → агент shutdown → заглушка RO`; shutdown завис → `force`;
- слоты не привязаны: тот же студент идёт в другой слот, слот — к другому студенту;
- размещение по кругу узлов, пропуск узла без места, лимиты памяти гостей и vCPU узла, резерв ещё не
  запущенных слотов, offline-узел, нехватка ресурсов;
- следующая сессия продолжает тот же проект и в том же, и в другом слоте (слот 0.4.0); со слотом
  0.3 в другом слоте — понятная ошибка `SLOT_COLD_RESUME_UNSUPPORTED`;
- `student_key`: только безопасный компонент пути по правилам агента;
- выдача: конкурентная (8 студентов на 5 слотов), порядок кандидатов, fencing, идемпотентный `POST /sessions`;
- восстановление: потерян ответ на старт (второго старта нет), рестарт менеджера посреди deploy,
  чужой каталог в `mp1` у запущенного CT, агент занят и миграция CT, повтор deploy с тем же
  Idempotency-Key, `STALE_IDEMPOTENCY_KEY`, `OPERATION_IN_PROGRESS`, FAILED в слоте, тайм-аут
  этапа, Proxmox недоступен, maxduration, остановка до и во время старта;
- просьбы плагина (`docs/PLUGIN_REQUESTS.md`): `info`, строгие тела и `unsupported_field`,
  `options.max_duration` с ограничением, `stage`/`progress`, коды `error` с `retryable`, список сессий
  активности, `conflict`, `node_not_running`, `nodes_stale`, остановка преподавателем;
- контракт SPEC §3: авторизация (сайт ↔ секрет), коды и формат ошибок, идемпотентность,
  изоляция сайтов, формат сессии и `nodes`, билеты (живые `node_id`, `readonly`, тело запроса к шлюзу);
- конфиг: fail closed, права 0600, секреты не попадают в сообщения об ошибках.
- факты лабы (`tests/test_facts.py`): разбор ответа слота (битые записи пропускаются), журнал только при
  изменении данных или состояния сбора, один `rev` на чтение, каскад от сессии, `GET …/state` с `since_rev` (304)
  и для остановленной сессии, `POST …/state/refresh` (слияние запросов, передача слоту), последнее чтение перед
  остановкой, слот без `/facts` (404) ничего не ломает.
- VNC-консоль (`tests/test_vnc.py`, `gateway/tests/check_gateway.py`): `vnc` только у `qemu`-образов; `console` в узле слота
  только у `vnc`-нод (и проходит модель `DeployTicket` слота), неизменные `lab_revision` лаб без них (значения
  закреплены в тесте, и для лабы из Moodle, и для эталонной); `403 console_disabled` наблюдателю, билет владельца с
  `console: vnc`, порядок проверок билета; шлюз: двоичные кадры в обе стороны для `vnc`, `400` на `vnc` с `readonly`
  и на неизвестный `console`.

Требует стенда (тестами не подтверждено):
- настоящий агент: mTLS, длительность `pct start`/действий, формат `mp1` в конфиге CT после `pct set`;
- реальный PVE: права токена из §5, `/cluster/resources` с узлами, применение `cores`/`memory`;
- CephFS: владелец каталога студента (uid `gns3` слота), запись в `/opt/gns3/projects` из слота; `.slot-locks` в каталоге студента (DESIGN §5);
- настоящий Lab Slot и GNS3: длительность deploy, `readyz` после старта LXC, путь кода (DESIGN §11, В1);
- VNC на стенде: слот 0.8.0 (нода GNS3 с `console_type: vnc`), noVNC в браузере через nginx и шлюз (RFB целиком, смена размера, долгая сессия);
- история фактов в `session_facts_log` на настоящем стенде: одна запись на ноду, пока в госте ничего не меняется, новая после смены адреса или состояния интерфейса;
- шлюз консолей в LXC менеджера (§1.1): unit, nginx и `gateway-env` на настоящем systemd и с настоящими
  слотами.

Подтверждено на стенде (слот 0.6.0, GNS3 2.2.61): `GET /v1/labs/{id}/facts` у лабы с Debian-нодами; `port` определяется по MAC адаптера GNS3 (адаптер N = `mac_address` + N: нода с четырьмя адаптерами, `ens3`…`ens6` = адаптеры 0…3, у каждого верный `port`); `admin`/`oper` интерфейсов, поднятых и не поднятых в госте, совпадают с действительностью.

## Производные образы QEMU (0.14.0)

Контракт — `docs/CONTRACTS.md` §2.8; требуется агент 1.3 с `images_capture`.
Миграция 009 применяется обычной административной командой до запуска менеджера.
В `IMAGES_FILE` у снимаемой QEMU-базы задайте `sha256: "sha256:<64 hex>"`
(хеш файла на CephFS, полученный администратором при установке образа).
Без него validate и обычные запуски работают, capture отклоняется как `not_capturable`.
`capture: false` запрещает снятие, по умолчанию QEMU разрешён.
`AUTHORING_MAX_DURATION=240` — минуты, `IMAGE_MAX_BYTES=8589934592` — предел
файла до и после rebase, `IMAGE_CAPTURE_MAX_NODES=10` — предел запроса.
Образы из БД отдаются немедленно, включая retired для старых лаб. Лимит сайта
проверяется по базе. Каталог изолирован по сайту Moodle.

Запрос снятия и промежуточные результаты сохраняются в БД, их подхватывает
оркестратор после перезапуска. До результата каталог не меняется. Если ответ
агента потерян после публикации файла, операция завершается ошибкой; повтор
failed-capture выдаёт новые имена. Непривязанные файлы учитываются оператором
и автоматически не удаляются. Проект автора не меняется.

## Штатное выключение перед capture (0.15.0)

Миграция 010 хранит историю запуска нод в workspace и последний pre-stop статус
в сессии. Перед capture работающей сессии выбранные гости выключаются через
маршрут guest-shutdown слота 0.14.1, параллельно, затем проверяется stopped в GNS3.
`IMAGE_SHUTDOWN_TIMEOUT=120` (30–300 с) — бюджет выключения гостей.
При ошибке GUEST_NOT_STOPPED сессия остаётся running, файлы не снимаются.
Автор может повторить выключение через слот или явно задать force. Без QGA доступно
«Сохранить как есть»; ручной poweroff роняет лабу и в авторском запуске.
Из stopped-сессии с последними started-нóдами без force приходит 409 guest_not_stopped.
Результат forced и derived.forced отмечают небезопасное снятие. Старые образы
до миграции 010 отмечаются forced консервативно: прежний pipeline QGA не использовал.
