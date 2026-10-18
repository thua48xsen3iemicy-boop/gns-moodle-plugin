# Lab Manager — прототип 0.4

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

Непривилегированный CT с Ubuntu 24.04. RO CephFS с кодом и лабами задаёт администратор:

```sh
pct set <MGR_VMID> -mp0 /mnt/pve/cephfs/gns3-ro,mp=/srv/gns3-ro,ro=1
```

Каталоги студентов менеджеру не нужны: монтирует их агент, а создаёт сторона Proxmox (п. 2).
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
  `sites.json`, `GW_INTERNAL_KEY` = `GATEWAY_INTERNAL_KEY`, порт — из `GATEWAY_INTERNAL_URL`).
  После любого изменения `slots.json` или `sites.json` файл нужно сгенерировать заново и перезапустить шлюз.
- Шлюз работает под своим временным пользователем (`DynamicUser`), секретов менеджера (токен
  Proxmox, API-токены слотов) он не видит.
- Шлюз один на все слоты: билеты живут в его памяти, и менеджер рассчитан на один шлюз.

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

## 2. Сторона Proxmox: агент, слоты, каталоги

1. Агент на каждый узел — по `agent/README.md` (ветка `feature/proxmox-node-agent`):
   `make-demo-pki.sh` (или своя PKI), `install-node.sh`, `LAB_VMID_MIN/MAX`, firewall.
   Менеджеру из PKI нужны `manager.crt`, `manager.key` и `node-server-ca.crt`.
   Код слота агент должен монтировать в `/srv/gns3-ro/slot`; в ветке пока `/opt/slot`, это
   исправляет разработчик агента. Все запросы к агенту — в [`docs/AGENT_REQUESTS.md`](docs/AGENT_REQUESTS.md).
2. Слоты: CT из диапазона `LAB_VMID_MIN..MAX`, `unprivileged: 1`, `pct set <vmid> --tags labslot`,
   внутри — Lab Slot по `slot/README.md` и `/usr/local/sbin/lab-slot-init` из `agent/guest/`.
3. Каталоги на CephFS (агент требует, чтобы предки принадлежали root и не были доступны на
   запись группе и остальным):

   ```sh
   CEPH=/mnt/pve/data
   install -d -o root -g root -m 0755 $CEPH/students $CEPH/students/placeholder
   ```

   Каталоги студентов `students/<slug сайта>-<user_id>` создаёт агент по запросу менеджера
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
| `IMAGES_FILE` | каталог образов для лаб, которые преподаватели собирают в Moodle (пример — `examples/images.json`). Не задан — лабы только из `LABS_DIR`. Формат и проверки — `lab_manager/images.py` |
| `LAB_MEMORY_OVERHEAD_MB` | запас памяти CT для лабы из Moodle: память нод + 10 % + это значение (по умолчанию 1024) |
| `QUEUE_TIMEOUT` | сколько секунд сессия ждёт слот, потом `failed` с `QUEUE_TIMEOUT`; `0` — без ограничения (по умолчанию 600) |
| `PVE_NODES`, `PVE_TOKEN_ID`, `PVE_TOKEN_SECRET`, `PVE_CA_FILE` | точки входа API, токен, CA кластера |
| `AGENT_URL_TEMPLATE` | `https://{node}.lab.local:8443`; `{node}` — имя узла Proxmox, хост — как в SAN сертификата агента |
| `AGENT_CA_FILE`, `AGENT_CERT_FILE`, `AGENT_KEY_FILE` | `node-server-ca.crt`, `manager.crt`, `manager.key` |
| `LAB_CEPH_ROOT` | тот же, что у агентов (`/mnt/pve/data`): по нему менеджер точно сверяет источники `mp0`/`mp1` |
| `AGENT_START_TIMEOUT`, `AGENT_MOUNTS_TIMEOUT`, `AGENT_STOP_TIMEOUT` | 420/120/130 с — больше сумм таймаутов внутри агента |
| `AGENT_START_ACTIONS` | действия в госте после `pct start`; по умолчанию `init_slot` (`agent/guest/lab-slot-init`) |
| `SLOT_CODE_REVISION` | пусто: общий `<ceph>/slot`; иначе `<ceph>/slot/<revision>` |
| `PLACEHOLDER_STUDENT_KEY` | что монтировать RO в освобождённый слот (`placeholder`; пусто — не перемонтировать) |
| `NODE_MEMORY_RESERVE_MB` | сколько памяти оставлять свободной на каждом узле при размещении |
| `GATEWAY_INTERNAL_URL`, `GATEWAY_INTERNAL_KEY`, `GATEWAY_PUBLIC_URL` | `/internal/tickets` шлюза, его `GW_INTERNAL_KEY`, `wss://` для браузера |
| `DEFAULT_MAX_DURATION`, `MAX_DURATION_LIMIT` | длительность сессии по умолчанию и предел для `options.max_duration` от плагина, минуты |

## 5. Токен Proxmox

Запуском, остановкой и монтированием занимается агент, поэтому токен — обычный пользователь
(не root) с правами только на пул слотов и на узлы:

```sh
pveum role add LabManager -privs "VM.Audit VM.Config.CPU VM.Config.Memory"
pveum role add LabManagerNodes -privs "Sys.Audit"
pveum user add lab-manager@pve
pveum pool add lab-slots
pveum pool modify lab-slots -vms 2001,2002,2003
pveum user token add lab-manager@pve manager --privsep 1     # секрет показывается один раз
pveum acl modify /pool/lab-slots -token 'lab-manager@pve!manager' -role LabManager
pveum acl modify /nodes -token 'lab-manager@pve!manager' -role LabManagerNodes
```

- `VM.Audit` — `/cluster/resources` (CT), статус и конфиг CT (проверка `mp1`);
- `VM.Config.CPU`, `VM.Config.Memory` — подгонка `cores`/`memory` под лабу;
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
$ADM labs                # проверка каталога лаб (lab.gns3 ↔ ticket.json)
$ADM slots               # реестр слотов
$ADM slot-enable 2001    # maintenance/failed → free (агент подтверждает, что CT выключен)
$ADM slot-maintenance 2001
```

Новый слот из `slots.json` появляется в состоянии `maintenance`, пока оператор его не включит.
При сбое сессии менеджер сам останавливает лабу в слоте (если она могла там появиться) и CT и
монтирует заглушку: каталог студента не остаётся подключённым к работающему CT (DESIGN §6.4).
Слот при этом остаётся `failed`. Оператор разбирается (журнал слота, `GET /v1/operations/…`,
`events` менеджера) и включает слот (`slot-enable`). Студент тем временем может запустить лабу на
любом другом слоте.

Если CT остановить не удалось, `slots` показывает у слота `student_key`. Пока оператор не
выключит CT и не сделает `slot-enable`, этот студент получает `STUDENT_DIR_BUSY`: его каталог не
монтируется во второй слот.

**Нужен Lab Slot 0.4.0 или новее.** Следующая сессия студента всегда продолжает его проект
(DESIGN §6.1), в каком бы слоте она ни запустилась; настройки `LAB_PERSISTENCE` больше нет (если она
осталась в `manager.env` со значением, отличным от `resume`, менеджер не стартует). Проекты, которые
создал слот 0.3 в старом режиме `fresh` (по проекту на сессию), продолжаться не будут: это просто
старые каталоги в `students/<key>/`, их можно удалить вручную.

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
- размещение best fit по памяти узлов, резерв ещё не запущенных слотов, offline-узел, нехватка ресурсов;
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

Требует стенда (тестами не подтверждено):
- настоящий агент: mTLS, длительность `pct start`/действий, формат `mp1` в конфиге CT после `pct set`;
- реальный PVE: права токена из §5, `/cluster/resources` с узлами, применение `cores`/`memory`;
- CephFS: владелец каталога студента (uid `gns3` слота), запись в `/opt/gns3/projects` из слота; `.slot-locks` в каталоге студента (DESIGN §5);
- настоящий Lab Slot и GNS3: длительность deploy, `readyz` после старта LXC, путь кода (DESIGN §11, В1);
- шлюз консолей в LXC менеджера (§1.1): unit, nginx и `gateway-env` на настоящем systemd и с настоящими
  слотами.
