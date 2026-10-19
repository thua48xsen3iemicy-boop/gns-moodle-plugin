# Памятка оператора: разбор проблем

Команды — от root на ноде Proxmox. `pct exec` работает только на той ноде, где живёт CT: команды
менеджера — на ноде менеджера, команды слота — на ноде слота.

```sh
MGR=1900    # MANAGER_VMID из secrets/lab.conf
ADM="runuser -u lab-manager -- /srv/gns3-ro/manager/lab-manager-admin"
SQL="runuser -u lab-manager -- env LC_ALL=C.UTF-8 psql -X -P pager=off lab_manager"   # без C.UTF-8 кириллица выходит байтами <D0><9B>
P=/mnt/pve/cfs/gns3-ro/install/lab-provision
```

## NO_SLOTS: «Нет ни одного рабочего слота»

### Что это значит

`NO_SLOTS` — это **не** «все слоты заняты». Когда слоты заняты, сессия стоит в очереди
(«Ожидание свободного слота…»), а через `QUEUE_TIMEOUT` секунд падает с кодом `QUEUE_TIMEOUT`.

`NO_SLOTS` менеджер отдаёт сразу, если в его БД нет ни одного слота, который есть в `slots.json` и
не находится в `failed` или `maintenance` (`store.usable_slots()`). Выключенный CT — обычное
состояние свободного слота (`free`), сам по себе он к `NO_SLOTS` не приводит.

Причин три:

1. **Слот ни разу не включили.** Новый слот из `slots.json` попадает в БД в состоянии
   `maintenance` и ждёт `enable`. Это самый частый случай после установки или добавления ноды.
2. **Слот ушёл в `failed` после сбоя сессии.** Менеджер сам останавливает лабу и CT, монтирует
   заглушку и оставляет слот оператору. В `maintenance` слот уходит, если заглушку не удалось
   подтвердить в конфиге CT (`placeholder not confirmed`).
3. **Слота нет в `slots.json`** (`in_config: false`): VMID поменяли, а `secrets` + `manager` не
   повторили.

### 1. Реестр слотов

```sh
pct exec $MGR -- $ADM slots
```

Одна строка JSON на слот:

```json
{"vmid": 2001, "address": "10.10.12.21", "state": "maintenance", "generation": 0, "session_id": null,
 "student_key": null, "in_config": true, "state_message": ""}
```

| Что в строке | Что это значит | Что делать |
|---|---|---|
| `maintenance`, `generation: 0`, `state_message` пуст | слот не включали | §4 |
| `failed`, `state_message: "session <id>: <CODE>"` | сессия `<id>` упала с `<CODE>`, слот оставлен оператору | §2, §3, потом §4 |
| `maintenance`, `"session <id>: placeholder not confirmed"` | после сессии в `mp1` не подтвердилась заглушка | §3 (`pct config`), потом §4 |
| `in_config: false` | слота нет в `/etc/lab-manager/slots.json` | `SLOTS` в `lab.conf`, `$P secrets`, `$P manager` |
| `student_key` не `null` | CT мог остаться с каталогом студента на запись; этот студент получает `STUDENT_DIR_BUSY` | §3: выключить CT, потом §4 |
| хотя бы один `free`/`preparing`/`ready`/`busy`/`releasing` | `NO_SLOTS` этот менеджер сейчас не отдаст | ошибка у студента старая, или плагин ходит в другой менеджер |

То же из БД, с временем последнего изменения:

```sh
pct exec $MGR -- $SQL -c "SELECT vmid, state, in_config, session_id, student_key, state_message, updated_at FROM slots ORDER BY vmid"
```

### 2. Почему слот в `failed`: сессия, события, журнал

`session_id` слота (и `<id>` в `state_message`) — сессия, после которой слот вывели из работы.

```sh
# Последние упавшие сессии: на каком этапе и с каким кодом
pct exec $MGR -- $SQL -c "SELECT id, slot_vmid, slot_node, error->>'stage' AS stage, error->>'code' AS code, error->>'message' AS message, finished_at FROM sessions WHERE status = 'failed' ORDER BY created_at DESC LIMIT 10"

# История слота: выдача, этапы, сбой, уборка (park_failed — не удалось вернуть заглушку)
pct exec $MGR -- $SQL -c "SELECT at, session_id, kind, detail FROM events WHERE slot_vmid = 2001 ORDER BY id DESC LIMIT 30"

# Журнал менеджера: строки "Session <id> failed at <stage>: <CODE>" и "not parked; maintenance"
pct exec $MGR -- journalctl -u lab-manager --since "-2h" --no-pager | grep -E "failed at|not parked|Traceback|ERROR"
```

По `stage` понятно, на чём сломалось:

| `stage` | Где смотреть |
|---|---|
| `ct_prepare`, `ct_start`, `ct_stop` | агент ноды: `journalctl -u lab-node-agent -n 200` на ноде слота (`slot_node`) |
| `student_dir` | агент: каталог студента (`LAB_STUDENT_HOST_UID/GID`, права на `gns3-rw`) |
| `slot_ready`, `lab_wait`, `lab_stop_wait` | сам слот: `journalctl -u lab-slot` в CT (§3) |
| код `TIMEOUT` | этап не уложился в свой таймаут — смотреть журнал того, кто отвечает за этап |

### 2а. `SLOT_LAB_FAILED`: что сломалось в самом слоте

Менеджер видит только состояние лабы `FAILED`, причину знает слот. Сначала текст ошибки сессии:

```sh
pct exec $MGR -- $SQL -c "SELECT id, slot_vmid, slot_node, student_key, project_id, error->>'stage' AS stage, error->>'message' AS message, started_at, finished_at FROM sessions WHERE id = 's-58c5120c0175b98c'"
```

- `stage = ready`, «Лаба в слоте перешла в FAILED: `<code>`» — лаба работала и упала. Код — от слота:
  - `RuntimeError` — монитор слота (раз в 10 с) нашёл ноду GNS3 остановленной или пропавшей:
    QEMU/IOU упал, не хватило памяти в CT, сломался образ;
  - `SERVICE_RESTART` — `lab-slot` (или `gns3`, от которого он зависит, или весь CT)
    перезапустился, пока лаба работала;
  - `UNKNOWN` — слот не знает проект (404).
- `stage = lab_submit`/`lab_wait`/`lab_stop`, «Лаба в слоте в состоянии FAILED, нужен оператор» —
  лаба была `FAILED` уже на момент запуска или остановки.

Полная ошибка слота лежит в каталоге студента на CephFS, её видно с любой ноды без запуска CT:

```sh
F=/mnt/pve/cfs/gns3-rw/students/<student_key>/.lab-slot/labs/<project_id>.json
python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); print(d["state"], d.get("stage"), d.get("error"))' "$F"
```

Журналы слота — из rootfs выключенного CT, тоже без запуска (на ноде слота):

```sh
pct mount 701
R=/var/lib/lxc/701/rootfs
journalctl -D $R/var/log/journal -u lab-slot -u gns3 --since "-1d" --no-pager | tail -200
journalctl -D $R/var/log/journal -k --since "-1d" --no-pager | grep -iE "oom|killed process"
# Операции слота (deploy/stop) с ошибками:
python3 -c 'import sqlite3,sys; [print(r[0]) for r in sqlite3.connect("file:"+sys.argv[1]+"?mode=ro", uri=True).execute("SELECT data FROM records WHERE kind = '"'"'operation'"'"'")]' $R/var/lib/lab-slot/state.sqlite3 | tail -5
pct unmount 701
```

Если в `/var/log/journal` пусто, журнал в CT не сохраняется между запусками: тогда остаются файл
лабы и SQLite.

Сразу несколько слотов в `SLOT_LAB_FAILED` примерно в одно время — ищите общую причину:
`git pull` или `lab-provision slot` во время сессий, нехватка памяти на ноде, общий образ в `mp2`.

### 3. CT слота и агент (на ноде слота)

```sh
pct status 2001                         # для включения слот должен быть stopped
pct config 2001 | grep -E '^mp[0-9]'    # mp0 — код (ro=1); mp1 — .../students/placeholder, ro=1
journalctl -u lab-node-agent -n 200 --no-pager
```

- `mp1` указывает на `students/<сайт>-<user_id>`, а не на `placeholder` — заглушку не вернули
  (`placeholder not confirmed`, `park_failed`). Её вернёт следующий старт через агента; главное —
  чтобы CT был выключен.
- CT запущен — сначала посмотреть журнал слота, потом выключить:

  ```sh
  pct exec 2001 -- journalctl -u lab-slot -n 200 --no-pager
  pct shutdown 2001 --timeout 60 || pct stop 2001
  ```

- Связь менеджер → агенты, слоты и Proxmox целиком: `$P check` на ноде менеджера.

### 4. Вернуть слот в работу

Только после того, как причина понятна и устранена — иначе следующая сессия снова уронит слот.

```sh
$P enable 2001                          # на ноде менеджера; без VMID — все слоты из SLOTS
# или напрямую:
pct exec $MGR -- $ADM slot-enable 2001
pct exec $MGR -- $ADM slots             # теперь state: free
```

`slot-enable` переводит `maintenance`/`failed` → `free` и снимает `student_key`, но сначала
спрашивает у агента состояние CT. Ошибки:

| Ошибка | Что делать |
|---|---|
| `CT 2001 is running on pve1; stop it first` | §3: выключить CT |
| `Slot is in use or unknown` | на слоте живая сессия (`SELECT id, status, stage FROM sessions WHERE slot_vmid = 2001 AND status NOT IN ('stopped', 'failed')`), слот в `preparing`/`busy`/`releasing`, или VMID нет в БД |
| ошибка TLS / соединения с агентом | `$P check`, `journalctl -u lab-node-agent` на ноде слота |

Вывести слот из работы вручную (например, на время обслуживания CT):
`pct exec $MGR -- $ADM slot-maintenance 2001 --message "обновляю образы"`.

## Похожие коды и сообщения

| У студента | Причина |
|---|---|
| «Ожидание свободного слота…», потом `QUEUE_TIMEOUT` | все рабочие слоты заняты |
| «Недостаточно ресурсов на узлах, ожидание…» | свободные слоты есть, но на их нодах не хватает памяти или CPU (`NODE_MEMORY_RESERVE_MB`, `NODE_GUEST_MEMORY_MB`, `NODE_CPU_OVERCOMMIT`) |
| «Proxmox недоступен, ожидание…» | менеджер не достучался до API Proxmox (`PVE_NODES`, токен) |
| `STUDENT_DIR_BUSY` | у какого-то слота в `slots` стоит `student_key` этого студента: §3, потом §4 |
