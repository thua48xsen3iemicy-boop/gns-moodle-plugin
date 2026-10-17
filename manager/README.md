# Lab Manager — прототип 0.2

Сервис между Moodle (`plugin/`) и слотами Lab Slot (`slot/`). Принимает запросы плагина по
контракту `plugin/docs/SPEC.md` §3, подбирает для лабы свободный слот на узле, где хватает
памяти, через агента на узле Proxmox (Lab Node Agent, mTLS) монтирует каталог студента и
запускает LXC, запускает и останавливает лабу в Lab Slot, выдаёт билеты шлюзу консолей.
Слоты не привязаны к студентам. Как всё устроено и почему — в [`docs/DESIGN.md`](docs/DESIGN.md),
там же открытые вопросы.

Стек: Python 3.12, FastAPI, Uvicorn, httpx, pydantic, psycopg 3, PostgreSQL (локально в LXC менеджера).

## Размещение

| Что | Где |
|---|---|
| Код и venv | `/srv/gns3-ro/manager` (RO CephFS), venv собирается по этому же пути |
| Конфиг и секреты | `/etc/lab-manager/` (`manager.env`, `sites.json`, `slots.json`, `pve-root-ca.pem`, `agent-ca.pem`, `manager.crt`, `manager.key`) — не в Git и не на CephFS |
| PostgreSQL | локально в этом LXC, данные — `/var/lib/postgresql` на rootfs (RBD), не на CephFS |
| Каталог лаб | `/srv/gns3-ro/labs/<lab_ref>/` (RO CephFS) |
| Каталоги студентов | `/srv/lab-students` = `<LAB_CEPH_ROOT>/students` (RW CephFS) |

Обновление кода: остановить сервис → заменить `/srv/gns3-ro/manager` → `admin migrate`, если
есть новые миграции → запустить сервис.

## 1. LXC менеджера (на узле PVE, root)

Непривилегированный CT с Ubuntu 24.04. Точки монтирования задаёт администратор. Пути на хосте
ниже — пример; `LAB_CEPH_ROOT` агента по умолчанию `/mnt/pve/data`.

```sh
pct set <MGR_VMID> -mp0 /mnt/pve/cephfs/gns3-ro,mp=/srv/gns3-ro,ro=1
pct set <MGR_VMID> -mp1 /mnt/pve/data/students,mp=/srv/lab-students
```

Менеджеру нужен сетевой доступ к API Proxmox (8006), к агентам узлов (порт из
`AGENT_URL_TEMPLATE`), к слотам (8080) и к внутреннему API шлюза.

## 2. Каталоги студентов (на узле PVE, root, один раз)

Права даются через общую группу `labdata` (DESIGN §5). Её GID одинаков во всех слотах и в
менеджере: ниже 2000 внутри CT, то есть 102000 на хосте при стандартной idmap. `gns3` в слотах
и `lab-manager` в менеджере — её члены.

```sh
CEPH=/mnt/pve/data
install -d -o 101001 -g 102000 -m 2770 $CEPH/students              # 101001 = uid lab-manager (1001)
install -d -o 101001 -g 102000 -m 2750 $CEPH/students/placeholder  # пустая заглушка, RO
```

`students/<site>-<user_id>/` и `results/` внутри него менеджер создаёт сам при первом
запуске сессии.

Слоты (CT с vmid из `LAB_VMID_MIN..MAX` агента, непривилегированные, с тегом `labslot`) готовит
администратор: Lab Slot по `slot/README.md`, группа `labdata` у `gns3`, служба `lab-slot`.
Точки монтирования `mp0`/`mp1` при каждом старте ставит агент.

## 3. Установка в LXC менеджера

```sh
apt install -y python3.12-venv postgresql
useradd --system --uid 1001 --home-dir /nonexistent --shell /usr/sbin/nologin lab-manager

groupadd --gid 2000 labdata && usermod -aG labdata lab-manager

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
cp agent-ca.pem manager.crt manager.key /etc/lab-manager/
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
mTLS, каталог лаб, `STUDENT_ROOT` с правом записи, заглушка, версия схемы). Без `manager.env`,
секретов или с неверной схемой сервис не стартует (fail closed). `run.py` проверяет, что код
и venv лежат в `/srv/gns3-ro/manager`. Uvicorn работает в один процесс: оркестратор живёт в нём же.
`ReadWritePaths=/srv/lab-students` — поправьте, если `STUDENT_ROOT` в другом месте.

Готовность: `curl -s 127.0.0.1:8090/readyz` (200 `ready` / 503 `not_ready` с причинами).

## 4. Конфигурация

`manager.env` (полный список — в `manager.env.example`):

| Переменная | Смысл |
|---|---|
| `DATABASE_URL` | `postgresql:///lab_manager` (сокет, peer) |
| `MANAGER_SITES_FILE` | сайты Moodle: `url` (= `$CFG->wwwroot`), `slug` (часть `student_key`), `secret` (Bearer) |
| `MANAGER_SLOTS_FILE` | слоты: `vmid`, `address` (= `SLOT_LISTEN_HOST` слота и ключ в `GW_SLOTS` шлюза), `port`, `api_token`, `console_token` |
| `LABS_DIR` | каталог лаб; `meta.json` может задавать `resources: {cores, memory_mb}` |
| `PVE_NODES`, `PVE_TOKEN_ID`, `PVE_TOKEN_SECRET`, `PVE_CA_FILE` | точки входа API, токен, CA кластера |
| `AGENT_URL_TEMPLATE` | `https://{node}.mgmt:8443`, где `{node}` — имя узла Proxmox |
| `AGENT_CA_FILE`, `AGENT_CERT_FILE`, `AGENT_KEY_FILE` | mTLS к агентам |
| `AGENT_START_ACTIONS` | действия в госте после `pct start` (`init_slot`, `daemon_reload`, `start_slot_service`, `restart_slot_service`) |
| `SLOT_CODE_REVISION` | пусто: общий `<ceph>/slot`; иначе `<ceph>/slot/<revision>` |
| `PLACEHOLDER_STUDENT_KEY` | что монтировать RO в освобождённый слот (`placeholder`; пусто — не перемонтировать) |
| `STUDENT_ROOT` | путь `<ceph>/students` внутри LXC менеджера |
| `LAB_PERSISTENCE` | `fresh` (новый проект в каждой сессии) или `resume` (тот же проект; нужен cold resume в слоте, DESIGN §6.1) |
| `NODE_MEMORY_RESERVE_MB` | сколько памяти оставлять свободной на каждом узле при размещении |
| `GATEWAY_INTERNAL_URL`, `GATEWAY_INTERNAL_KEY`, `GATEWAY_PUBLIC_URL` | `/internal/tickets` шлюза, его `GW_INTERNAL_KEY`, `wss://` для браузера |
| `DEFAULT_MAX_DURATION` | длительность сессии, минуты |

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
После `failed` менеджер ничего не делает сам. Оператор разбирается со слотом (статус лабы,
`GET /v1/operations/…`), при необходимости останавливает лабу в слоте и CT (через агента или
`pct`) и включает слот (`slot-enable`). Студент тем временем может запустить лабу на любом другом слоте.

## 7. Тесты

```sh
cd manager
python3 -m venv /tmp/lm-venv && /tmp/lm-venv/bin/pip install -r requirements.txt pytest
/tmp/lm-venv/bin/python -m pytest -q tests                      # без БД: тесты PostgreSQL пропускаются
DATABASE_URL=postgresql:///lab_manager_test /tmp/lm-venv/bin/python -m pytest -q -rs tests
```

**Внимание:** `DATABASE_URL` для тестов — только одноразовая база. Тесты пересоздают её схему `public`.

Proxmox, агент узла, Lab Slot и шлюз подменяются через `httpx.MockTransport` (`tests/fakes.py`).
Что проверено тестами:
- перебор нод Proxmox: первая не отвечает → вторая; последняя рабочая — первой; 4xx и 500 не
  переключают; 503 на GET переключает; изменяющий запрос с неизвестным исходом не повторяется;
  нода возвращается после `PVE_RETRY_AFTER`; все ноды недоступны; миграция CT;
- UPID: ожидание `stopped`/`OK`, ошибка `exitstatus`, недоступный узел задачи и таймаут → «неизвестно»;
- агент: формат запросов и таймауты, разбор всех ошибок `agent.py` (403/404/409×3/422/503/504);
- порядок `stop (если CT запущен) → cores/memory → агент start (mounts + pct start) → readyz → deploy`
  и `slot stop → агент shutdown → заглушка RO`; shutdown завис → `force`;
- слоты не привязаны: тот же студент идёт в другой слот, слот — к другому студенту;
- размещение best fit по памяти узлов, резерв ещё не запущенных слотов, offline-узел, нехватка ресурсов;
- режимы `fresh`/`resume`: новый проект в каждой сессии; тот же проект в том же слоте;
  понятная ошибка `SLOT_COLD_RESUME_UNSUPPORTED` в другом слоте;
- защита путей: `..`, симлинки, `student_key` по правилам агента, права `results/` (2750);
- выдача: конкурентная (8 студентов на 5 слотов), порядок кандидатов, fencing, идемпотентный `POST /sessions`;
- восстановление: потерян ответ на старт (второго старта нет), рестарт менеджера посреди deploy,
  чужой каталог в `mp1` у запущенного CT, агент занят и миграция CT, повтор deploy с тем же
  Idempotency-Key, `STALE_IDEMPOTENCY_KEY`, `OPERATION_IN_PROGRESS`, FAILED в слоте, тайм-аут
  этапа, Proxmox недоступен, maxduration, остановка до и во время старта;
- контракт SPEC §3: авторизация (сайт ↔ секрет), коды и формат ошибок, идемпотентность,
  изоляция сайтов, формат сессии и `nodes`, билеты (живые `node_id`, `readonly`, тело запроса к шлюзу);
- конфиг: fail closed, права 0600, секреты не попадают в сообщения об ошибках.

Требует стенда (тестами не подтверждено):
- настоящий агент: mTLS, длительность `pct start`/действий, формат `mp1` в конфиге CT после `pct set`;
- реальный PVE: права токена из §5, `/cluster/resources` с узлами, применение `cores`/`memory`;
- CephFS: группа `labdata` и setgid, видимость каталога студента в слоте под `gns3`;
- настоящий Lab Slot и GNS3: длительность deploy, `readyz` после старта LXC, путь кода (DESIGN §11, В1);
- шлюз консолей с реальными `GW_SLOTS`, systemd unit, preflight в LXC.
