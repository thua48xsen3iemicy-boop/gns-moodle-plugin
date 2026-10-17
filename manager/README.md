# Lab Manager — прототип 0.1

Сервис между Moodle (`plugin/`) и слотами Lab Slot (`slot/`). Принимает запросы плагина по
контракту `plugin/docs/SPEC.md` §3, выдаёт студентам LXC-слоты через Proxmox VE API,
подключает каталог студента, запускает и останавливает лабу в Lab Slot, выдаёт билеты шлюзу
консолей. Как всё устроено и почему — в [`docs/DESIGN.md`](docs/DESIGN.md), там же открытые вопросы.

Стек: Python 3.12, FastAPI, Uvicorn, httpx, pydantic, psycopg 3, PostgreSQL (локально в LXC менеджера).

## Размещение

| Что | Где |
|---|---|
| Код и venv | `/srv/gns3-ro/manager` (RO CephFS), venv собирается по этому же пути |
| Конфиг и секреты | `/etc/lab-manager/` (`manager.env`, `sites.json`, `slots.json`, `pve-root-ca.pem`) — не в Git и не на CephFS |
| PostgreSQL | локально в этом LXC, данные — `/var/lib/postgresql` на rootfs (RBD), не на CephFS |
| Каталог лаб | `/srv/gns3-ro/labs/<lab_ref>/` (RO CephFS) |
| Каталоги студентов и источники mount слотов | `/srv/lab-rw/{students,slots}` (RW CephFS, одна точка монтирования) |

Обновление кода: остановить сервис → заменить `/srv/gns3-ro/manager` → `admin migrate`, если
есть новые миграции → запустить сервис.

## 1. LXC менеджера (на узле PVE, root)

Непривилегированный CT с Ubuntu 24.04. Точки монтирования задаёт администратор (bind mount может
задать только `root@pam`, см. DESIGN §4.1):

```sh
# Пути на хосте — пример; подставьте свои.
pct set <MGR_VMID> -mp0 /mnt/pve/cephfs/gns3-ro,mp=/srv/gns3-ro,ro=1
pct set <MGR_VMID> -mp1 /mnt/pve/cephfs/lab,mp=/srv/lab-rw
```

`students/` и `slots/` должны лежать внутри **одной** точки монтирования (`mp1`), иначе
`rename(2)` между ними невозможен (preflight это проверяет).

## 2. Каталоги студентов и слоты (на узле PVE, root, один раз)

Служебный пользователь менеджера и `gns3` в слотах должны иметь **одинаковый числовой uid**
(DESIGN §4.4). Ниже uid 1001 внутри CT, то есть 101001 на хосте при стандартной idmap.

```sh
LAB=/mnt/pve/cephfs/lab
install -d -o 101001 -g 101001 -m 0750 $LAB/students $LAB/slots
for VMID in 201 202 203; do
  install -d -o 101001 -g 101001 -m 0750 $LAB/slots/$VMID
  install -d -o 101001 -g 101001 -m 0770 $LAB/slots/$VMID/mnt     # пустая заглушка
  pct set $VMID -mp0 $LAB/slots/$VMID/mnt,mp=/opt/gns3/student,backup=0
done
```

Точку монтирования слота менеджер больше не меняет: при выдаче он переносит в
`slots/<vmid>/mnt` каталог `work/` студента, при освобождении возвращает его обратно.

## 3. Установка в LXC менеджера

```sh
apt install -y python3.12-venv postgresql
useradd --system --uid 1001 --home-dir /nonexistent --shell /usr/sbin/nologin lab-manager

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
chown lab-manager:lab-manager /etc/lab-manager/sites.json /etc/lab-manager/slots.json
chmod 600 /etc/lab-manager/sites.json /etc/lab-manager/slots.json
chown root:lab-manager /etc/lab-manager/manager.env && chmod 640 /etc/lab-manager/manager.env
# Отредактировать: секреты (openssl rand -hex 32), адреса слотов и их токены, PVE_*, GATEWAY_*.

# Схема БД (и после каждого обновления кода с новыми миграциями):
sudo -u lab-manager /srv/gns3-ro/manager/lab-manager-admin migrate

cp /srv/gns3-ro/manager/lab-manager.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now lab-manager.service
```

Unit: `ExecStartPre` — preflight (конфиг, права 0600 на файлы секретов, CA, каталоги, одна файловая
система у `students` и `slots`, версия схемы). Без `manager.env`, секретов или с неверной
схемой сервис не стартует (fail closed). `run.py` проверяет, что код и venv лежат в
`/srv/gns3-ro/manager`. Uvicorn работает в один процесс: оркестратор живёт в нём же.
`ReadWritePaths=/srv/lab-rw` — поправьте, если `STUDENT_ROOT` в другом месте.

Готовность: `curl -s 127.0.0.1:8090/readyz` (200 `ready` / 503 `not_ready` с причинами).

## 4. Конфигурация

`manager.env` (полный список — в `manager.env.example`):

| Переменная | Смысл |
|---|---|
| `DATABASE_URL` | `postgresql:///lab_manager` (сокет, peer) |
| `MANAGER_SITES_FILE` | сайты Moodle: `url` (= `$CFG->wwwroot`), `slug` (имя в путях), `secret` (Bearer) |
| `MANAGER_SLOTS_FILE` | слоты: `vmid`, `address` (= `SLOT_LISTEN_HOST` слота и ключ в `GW_SLOTS` шлюза), `port`, `profile`, `api_token`, `console_token` |
| `LABS_DIR` | каталог лаб |
| `PVE_NODES` | точки входа `host:port` через запятую |
| `PVE_TOKEN_ID`, `PVE_TOKEN_SECRET`, `PVE_CA_FILE` | токен API и CA кластера |
| `STUDENT_MOUNT_MODE` | `handover` (по умолчанию), `pve_config`, `none` |
| `STUDENT_ROOT`, `SLOT_MOUNT_ROOT` | `/srv/lab-rw/students`, `/srv/lab-rw/slots` |
| `GATEWAY_INTERNAL_URL`, `GATEWAY_INTERNAL_KEY`, `GATEWAY_PUBLIC_URL` | `/internal/tickets` шлюза, его `GW_INTERNAL_KEY`, `wss://` для браузера |
| `DEFAULT_MAX_DURATION` | длительность сессии, минуты |

## 5. Токен Proxmox

Для `handover` хватает обычного пользователя (не root) с правами только на пул слотов:

```sh
pveum role add LabManager -privs "VM.Audit VM.PowerMgmt"
pveum user add lab-manager@pve
pveum pool add lab-slots
pveum pool modify lab-slots -vms 201,202,203
pveum user token add lab-manager@pve manager --privsep 1     # секрет показывается один раз
pveum acl modify /pool/lab-slots -token 'lab-manager@pve!manager' -role LabManager
```

`VM.Audit` нужен для `/cluster/resources`, статуса и конфига, `VM.PowerMgmt` — для start/shutdown/stop.
Статус своих задач (UPID) владелец токена читает без дополнительных прав.
Режиму `pve_config` нужен ещё `VM.Config.Disk`, но bind mount через токен Proxmox всё равно не
разрешит (DESIGN §4.1).

### Проверка на стенде (одна команда)

Подтвердить, что токен не может задать bind mount в вашей версии PVE:

```sh
curl -s --cacert /etc/lab-manager/pve-root-ca.pem -X PUT \
  -H "Authorization: PVEAPIToken=root@pam!test=SECRET" \
  --data-urlencode 'mp9=/tmp,mp=/mnt/test' https://pve1:8006/api2/json/nodes/pve1/lxc/<STOPPED_TEST_CT>/config
# ожидается 403: "mount point type bind is only allowed for root@pam"
```

## 6. Команды оператора

```sh
ADM="sudo -u lab-manager /srv/gns3-ro/manager/lab-manager-admin"
$ADM check-config        # сводка без секретов
$ADM labs                # проверка каталога лаб (lab.gns3 ↔ ticket.json)
$ADM slots               # реестр слотов
$ADM slot-enable 201     # maintenance/failed → free (CT выключен, заглушка mnt на месте)
$ADM slot-maintenance 201
$ADM student-detach 201 main 42   # вернуть work/ студента из выключенного слота после сбоя
```

Новый слот из `slots.json` появляется в состоянии `maintenance`, пока оператор его не включит.
После `failed` менеджер ничего не делает сам: оператор разбирается со слотом (статус лабы,
`GET /v1/operations/…`), при необходимости останавливает лабу и CT, возвращает каталог
(`student-detach`) и включает слот (`slot-enable`).

## 7. Тесты

```sh
cd manager
python3 -m venv /tmp/lm-venv && /tmp/lm-venv/bin/pip install -r requirements.txt pytest
/tmp/lm-venv/bin/python -m pytest -q tests                      # без БД: тесты PostgreSQL пропускаются
DATABASE_URL=postgresql:///lab_manager_test /tmp/lm-venv/bin/python -m pytest -q -rs tests
```

**Внимание:** `DATABASE_URL` для тестов — только одноразовая база. Тесты пересоздают её схему `public`.

Proxmox, Lab Slot и шлюз подменяются через `httpx.MockTransport` (`tests/fakes.py`).
Что проверено тестами:
- перебор нод Proxmox: первая не отвечает → вторая; последняя рабочая — первой; 4xx и 500 не
  переключают; 503 на GET переключает; изменяющий запрос с неизвестным исходом не повторяется;
  нода возвращается после `PVE_RETRY_AFTER`; все ноды недоступны; миграция CT;
- UPID: ожидание `stopped`/`OK`, ошибка `exitstatus`, недоступный узел задачи и таймаут → «неизвестно»;
- порядок `shutdown → mount → start → readyz → deploy` и `stop → shutdown → unmount`; CT
  выключен в момент mount/unmount (обе схемы: `handover` и `pve_config`);
- защита путей: `..`, симлинки на любом уровне, инъекция опций в `mpN`, чужой маркер, занятый слот,
  восстановление после сбоя между переименованиями;
- выдача слота: конкурентная выдача (8 студентов на 5 слотов), закрепление, профиль, fencing,
  идемпотентный `POST /sessions` при конкурентных запросах;
- восстановление: рестарт с задачей Proxmox в полёте (CT не стартует дважды), узел задачи
  недоступен, повтор deploy с тем же Idempotency-Key, `STALE_IDEMPOTENCY_KEY`,
  `OPERATION_IN_PROGRESS`, FAILED в слоте, maxduration, остановка до и во время старта;
- контракт SPEC §3: авторизация (сайт ↔ секрет), коды и формат ошибок, идемпотентность,
  изоляция сайтов, формат сессии и `nodes`, билеты (живые `node_id`, `readonly`, тело запроса к шлюзу);
- конфиг: fail closed, права 0600, секреты не попадают в сообщения об ошибках.

Требует стенда (тестами не подтверждено):
- реальный PVE: права токена из §5, формат UPID и `exitstatus`, поведение shutdown с `timeout`,
  проксирование между узлами и 595/596, миграция CT;
- CephFS: атомарность `rename` между `students/` и `slots/`, uid mapping (101001), поведение bind
  mount при переносе только на выключенном CT;
- настоящий Lab Slot и GNS3: длительность deploy/resume, `readyz` после старта LXC;
- шлюз консолей с реальными `GW_SLOTS`, systemd unit, preflight в LXC.
