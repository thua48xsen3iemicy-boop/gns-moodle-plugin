# Памятка оператора

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
2. **Слот ушёл в `failed` или `maintenance`.** Сбой лабы (`SLOT_LAB_FAILED`, таймаут лабы и т. п.)
   слот не выводит из работы: менеджер останавливает лабу и CT, агент откатывает CT к снимку чистого
   слота, менеджер монтирует заглушку, слот снова `free`. В `failed` слот остаётся, если сломался он
   сам (CT не запускается, отказ агента или Proxmox, не те монтирования, слот не стал готов). Такой
   слот **не откатывается**: CT выключен, заглушка стоит, а гость цел, и его журналы (§2, §2а, §3)
   на месте до `enable`. В `maintenance` слот уходит, если не подтвердились откат
   (`reset failed: SNAPSHOT_MISSING`, `RESET_UNSUPPORTED`, `PCT_FAILED`) или заглушка
   (`placeholder not confirmed`), или если подряд упали лабы стольких разных студентов, сколько
   задано в `SLOT_LAB_FAILURE_LIMIT` (`N labs of different students failed in a row`): тогда слот
   откатан, причину ищите в сессиях (§2).
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

Текст ошибки слота менеджер не сохраняет, а запись лабы в каталоге студента
(`<students>/<student_key>/.lab-slot/labs/<project_id>.json`) после сбоя перезаписывается: менеджер
сразу останавливает лабу, и там уже `STOPPED stopped None`. Причину видно только в журнале GNS3
**до** остановки. Конец журнала — это уборка менеджера (`POST …/nodes/stop`, `Stopping lab-slot`,
`Stopping gns3`), смотреть нужно то, что было раньше.

Журналы слота — из rootfs выключенного CT, без запуска (на ноде слота). Время в начале строки —
часовой пояс CT, время внутри строк `gns3server` — UTC, как в БД менеджера:

```sh
pct mount 701
R=/var/lib/lxc/701/rootfs
# От готовности лабы (started_at) до уборки (finished_at); --since/--until — во времени хоста
journalctl -D $R/var/log/journal -u lab-slot -u gns3 --since "<started_at> UTC" --until "<finished_at> UTC" --no-pager \
  | grep -vE '"GET /v2/|"GET /v1/'
journalctl -D $R/var/log/journal -k --since "-1d" --no-pager | grep -iE "oom|killed process"
pct unmount 701
```

Что искать:

- `QEMU process has stopped, return code: <не 0>`, `IOU … has stopped`, `Traceback` — нода упала сама;
- нода, которую уборка **не** останавливает (`Stopping QEMU VM "<имя>"` есть не для всех): она
  остановилась раньше, ищите её имя выше;
- `Started`/`Stopping` `gns3.service` или `lab-slot.service` посреди сессии — причина `SERVICE_RESTART`;
  кто перезапустил — соседние строки `systemd` и журнал хоста (`lab-provision`, `git pull`).

Если в `/var/log/journal` пусто, журнал в CT не сохраняется между запусками.

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
- CT не стартует: `Failed to open "/var/lib/lxc/2001/rootfs": Permission denied` и в `ls -ldn /var/lib/lxc/2001`
  права `drwx------`. Каталог создан с `umask 077`, а непривилегированному CT нужен `0755`
  (`lab-provision` теперь запускает `pct` с `umask 022`, но у CT, созданных старой версией, так и осталось):

  ```sh
  chmod 755 /var/lib/lxc/2001
  ```

### 4. Вернуть слот в работу

Только после того, как причина понятна и устранена — иначе следующая сессия снова уронит слот.

```sh
$P enable 2001                          # на ноде менеджера; без VMID — все слоты из SLOTS
# или напрямую:
pct exec $MGR -- $ADM slot-enable 2001
pct exec $MGR -- $ADM slots             # теперь state: free
```

`slot-enable` переводит `maintenance`/`failed` → `free` и снимает `student_key`, но сначала
спрашивает у агента состояние CT, откатывает его к снимку чистого слота и ставит заглушку. Поэтому
сначала прочитайте журналы (§2, §2а, §3), потом `enable`: после отката в госте их уже нет. Ошибки:

| Ошибка | Что делать |
|---|---|
| `CT 2001 is running on pve1; stop it first` | §3: выключить CT |
| `Slot is in use or unknown` | на слоте живая сессия (`SELECT id, status, stage FROM sessions WHERE slot_vmid = 2001 AND status NOT IN ('stopped', 'failed')`), слот в `preparing`/`busy`/`releasing`, или VMID нет в БД |
| `CT 2001 was not reset … SNAPSHOT_MISSING` | у слота нет снимка: `$P snapshot 2001` на ноде слота (слот выключен), потом снова `enable` |
| `CT 2001 was not reset … RESET_UNSUPPORTED` | агент ноды старше 1.2: обновить его (`$P agent`) |
| `CT 2001 was not reset … SNAPSHOT_UNSAFE` | в снимке нет `unprivileged: 1` или тега `labslot`: `$P slot 2001` пересоздаст снимок |
| ошибка TLS / соединения с агентом | `$P check`, `journalctl -u lab-node-agent` на ноде слота |

Вывести слот из работы вручную (например, на время обслуживания CT):
`pct exec $MGR -- $ADM slot-maintenance 2001 --message "обновляю образы"`.

### `lab-provision slot` или `snapshot`: `snapshot feature is not available`

Proxmox не делает снимок CT, у которого есть bind-монтирование (`mpN: /путь,mp=…`). Точки агента `mp0`,
`mp1` и с `IMAGES_DIR` `mp2` шаг убирает сам перед снимком. Если ошибка осталась, у слота есть чужое
bind-монтирование: шаг называет его (`has bind mounts (mp3)`), его нужно убрать из конфига слота
(`pct set <VMID> --delete mp3`) или заменить томом хранилища. Проверка: `pct config <VMID> | grep ^mp`.

## `COMMAND_UNCERTAIN`: слот в карантине

Агент записывает в `/var/lib/lab-node-agent/<VMID>.json` отметки `inflight`/`uncertain` (и `command`), когда
изменяющая команда `pct` (`start`, `exec`, `stop`, `shutdown`, `set`, `rollback`) не уложилась в тайм-аут. Пока отметка
стоит, агент отвечает `409 COMMAND_UNCERTAIN` на любое изменение и на `claim`. Автоматически она не снимается.

**Признаки.**

- У сессии `error.code` — `SLOT_QUARANTINED` (студент видит «Слот требует проверки администратором…»), слот в
  `maintenance`, `state_message` начинается с `SLOT_QUARANTINED`, у слота остаётся `student_key`.
- В `lab-db events <session>` есть `slot_quarantined` (`vmid`, `node`, `command`), а `ct_started` нет, если сбой был на
  запуске. У менеджера старше 0.11.1 вместо этого 10 минут `ownership_wait` («Нет подтверждения владения слотом…»)
  и `TIMEOUT`.
- Повторный запуск того же студента до сверки даёт `STUDENT_DIR_BUSY`: каталог может быть примонтирован на запись в
  работающем CT, и менеджер не даст его второму слоту.
- `$P check` печатает `QUARANTINED slot <VMID>: session …, student …, agent command …, mark present`.
- Ответ агента на `GET /slots/<VMID>/status` (mTLS, сертификат менеджера) содержит `"ownership": {…, "uncertain": true, "command": "exec"}`.

**Что делать.** На ноде слота; сначала выяснить, чем кончилась команда (`journalctl -u lab-node-agent`, журнал
слота в CT), и сохранить, что нужно из журналов: откат при `enable` их сотрёт.

```sh
pct status 501                          # CT может работать: слот поднялся сам, лаба шла
pct shutdown 501 --timeout 120          # или pct stop 501
ps -eo pid,args | grep -E 'pct|lxc-' | grep 501    # процессов pct/lxc с этим VMID быть не должно
$P reconcile 501                        # только показать, что будет сделано и что мешает
$P reconcile 501 --yes                  # снять inflight/uncertain/command, epoch и generation остаются
$P enable 501                           # на ноде менеджера: откат к снимку, заглушка, слот free
```

`reconcile` отказывает (ничего не меняя), если CT не `stopped`, есть процессы `pct`/`lxc-*` с этим VMID (монитор
остановленного CT не в счёт), агент занят слотом (держит свою блокировку), в отметке нет `uncertain`/`inflight`, а
слот в менеджере не `maintenance` и не `failed`. Перед правкой он кладёт копию записи в
`/root/lab-fence-<VMID>-<время>.json` (0600) и пишет новую запись атомарно (временный файл, `fsync`, `rename`, 0600).
Файл учёта вручную не удалять и `epoch`/`generation` не обнулять: протокол владения идёт дальше от них.
После `enable` ключ студента снимается обычным путём, и лаба запускается снова, проект продолжается.

Если карантин повторяется на одном слоте на запуске, а `command` — `exec`: действие запуска в госте идёт дольше
`LAB_GUEST_ACTION_TIMEOUT` агента (по умолчанию 120 с). Посмотрите, что тормозит (`lab-slot-init`, `systemctl start
lab-slot.service`), и только потом поднимайте предел (до 600 с, `AGENT_START_TIMEOUT` менеджера — не меньше бюджета
агента + 60 с, его покажет `$P check`).

## Добавить слоты на ту же ноду

Пример: на ноде уже есть слоты 701 и 702 (`10.19.36.71`, `.72`), добавляем 703 и 704 (`.73`, `.74`).
Всё — на этой ноде (на ней же менеджер). Пока работают шаги `agent` и `manager`, сессий на ноде
быть не должно: они перезапускают агент, менеджер и шлюз консолей.

**VMID.** Агент ноды принимает слоты в диапазоне от меньшего до большего VMID её слотов из `SLOTS`
(и только CT с тегом `labslot`). VMID менеджера не может попадать в этот диапазон — скрипт
остановится. Удобно брать следующие номера того же блока: 703, 704.

### 1. Клонировать CT

Источник — шаблон, из которого делали слоты, или выключенный слот. `pct clone` не копирует
bind-монтирования (`mp0`/`mp1` — код и каталог студента, `mp2` — образы), с ними клон не
сделать. Если клонируете слот, выведите его из работы и снимите их — агент вернёт их сам при
следующем старте:

```sh
pct exec $MGR -- $ADM slot-maintenance 701 --message "клонирование"
pct status 701                           # stopped
pct set 701 --delete mp0,mp1,mp2
```

Клон, новый IP и новый MAC (строка `net0` — как у источника, меняется только `ip=`, `hwaddr` убираем,
Proxmox выдаст новый):

```sh
for pair in 703=10.19.36.73 704=10.19.36.74; do
  id=${pair%=*} ip=${pair#*=}
  pct clone 701 $id --full 1 --hostname slot-$id      # --storage <rbd>, если нужно другое хранилище
  pct set $id -net0 "$(pct config $id | sed -n 's/^net0: //p' | sed -E "s/ip=[^,]*\//ip=$ip\//; s/,?hwaddr=[^,]*//")"
  pct config $id | grep -E '^(net0|tags|unprivileged)'   # статический ip=…, tags: labslot, unprivileged: 1
done
pct exec $MGR -- $ADM slot-enable 701                    # источник обратно в работу
```

Маска сети и шлюз в `net0` остаются как у источника. Адрес должен быть уникальным и доступным
менеджеру (порт 8080).

### 2. Уникализировать клоны

Клон уносит machine-id, SSH-ключи и состояние Lab Slot источника (`/var/lib/lab-slot`: операции и
ключи идемпотентности прошлых сессий). Чистим, пока CT выключен:

```sh
for id in 703 704; do
  pct mount $id
  R=/var/lib/lxc/$id/rootfs
  truncate -s 0 $R/etc/machine-id                 # systemd создаст новый при старте
  rm -f $R/var/lib/dbus/machine-id $R/etc/ssh/ssh_host_*
  rm -rf $R/var/lib/lab-slot/* $R/var/log/journal/*
  rm -f $R/etc/lab-slot/slot.env                  # шаг slot положит новый, с токенами этого слота
  pct unmount $id
done
```

SSH-ключи, если SSH в слоте нужен: после шага `slot` — `pct start 703 && pct exec 703 -- ssh-keygen -A && pct shutdown 703`.

### 3. Провизия

```sh
nano /mnt/pve/cfs/secrets/lab.conf        # SLOTS="701 702 703 704"
$P secrets                                # токены новых слотов, slots.json, пул lab-slots; старое не трогает
$P slot 703 704                           # только новые: тег, mp2, slot.env, lab-slot-init, проверки GNS3 и uid/gid gns3
$P agent                                  # новый диапазон VMID агента (701..704), агент перезапускается
$P manager                                # новый slots.json и gateway.env, менеджер и шлюз перезапускаются
$P check                                  # агент видит новые слоты, менеджер — их и Proxmox
$P enable 703 704                         # из maintenance в free
pct exec $MGR -- $ADM slots               # 703, 704: state free, in_config true
```

`venv slot` не повторяется: venv слота общий на CephFS.

Если `slot` или `agent` ругается на uid/gid `gns3` — клон сделан не из того шаблона: каталоги
студентов общие, uid/gid во всех слотах должны совпадать.

**Память ноды.** Новые слоты делят те же лимиты размещения (`NODE_GUEST_MEMORY_MB`,
`NODE_CPU_OVERCOMMIT` в `lab.conf`). Если лимит уже почти выбран, лишние слоты будут стоять
свободными, а сессии ждать «Недостаточно ресурсов на узлах».

## Собрать ZIP плагина и поставить в Moodle

### Сборка

Штатно — `plugin/devtools/prepare_zip.sh` (`plugin/devtools/README.md`): ставит в свой кеш Node 22 и исходники
Moodle 5.1, прогоняет ESLint и `grunt amd`, обновляет `plugin/plugin/amd/build` и пишет архив. Нужны Bash, Git, curl,
Python 3, `zip`, `flock` и доступ к GitHub и npm. Скрипт меняет файлы `amd/build` в рабочей копии, поэтому запускайте
его в отдельном клоне, а не в checkout стенда (тот должен оставаться чистым для `git pull` и сборки выпусков):

```sh
git clone /mnt/pve/cfs/gns3-ro /root/gns3-plugin-build      # уже есть — cd туда и git pull
cd /root/gns3-plugin-build
bash plugin/devtools/prepare_zip.sh         # печатает путь: plugin/dist/mod_gns3lab-<release>.zip
grep -E 'version|release' plugin/plugin/version.php
```

Скрипт кладёт `plugin/plugin/` в архив как одну папку `gns3lab/` — так ждёт установщик Moodle.
`plugin/dist/` в `.gitignore`. Если `amd/build` в Git уже собран штатно и `amd/src` после этого не менялся, хватит
упаковки без сборки: `sh plugin/devtools/build_zip.sh`. Для 0.8.0 так нельзя: его `amd/build` собран запасным путём.

Moodle обновляет плагин, только если `$plugin->version` в `version.php` больше установленного:
архив того же кода повторно ставить незачем. Установленная версия — «Администрирование →
Плагины → Обзор плагинов», строка «Лаба GNS3».

### Установка через веб-интерфейс

1. Скачать архив с ноды к себе: `scp root@<нода>:/root/gns3-plugin-build/plugin/dist/mod_gns3lab-*.zip .`
2. «Администрирование → Плагины → Установка плагинов» → загрузить ZIP → «Установить плагин из
   ZIP-файла». Тип плагина Moodle определит сам (модуль деятельности).
3. «Проверка» → «Продолжить» → «Обновить базу данных Moodle сейчас». Предупреждение про
   `MATURITY_ALPHA` ожидаемо.
4. Только при первой установке — настройки: «Администрирование → Плагины → Модули деятельности →
   Лаба GNS3 → Настройки». Адрес и секрет менеджера — из `/mnt/pve/cfs/secrets/moodle/plugin.txt`
   (`$P moodle` печатает его же).

Если установщик пишет, что каталог `mod/` недоступен для записи, — веб-сервер не может писать в
`public/mod` Moodle, тогда ставьте копированием (ниже).

### Установка копированием (Moodle — LXC на этой ноде)

```sh
MOODLE=<MOODLE_VMID>; ROOT=<корень Moodle, где config.php>
pct push $MOODLE /root/gns3-plugin-build/plugin/dist/mod_gns3lab-<release>.zip /tmp/gns3lab.zip
pct exec $MOODLE -- sh -c "cd $ROOT/public/mod && rm -rf gns3lab && unzip -q /tmp/gns3lab.zip && chown -R www-data:www-data gns3lab"
pct exec $MOODLE -- runuser -u www-data -- php $ROOT/admin/cli/upgrade.php --non-interactive
```

Если `admin/cli/upgrade.php` в корне нет, он в `public/admin/cli/` (раскладка Moodle 5.1).
Пользователь веб-сервера — `www-data` в Ubuntu/Debian, у вас может быть другой.

После обновления в браузере открывается старый JS — «Администрирование → Разработка → Сбросить все
кэши» и обновить страницу с Ctrl+F5.

## Похожие коды и сообщения

| У студента | Причина |
|---|---|
| «Ожидание свободного слота…», потом `QUEUE_TIMEOUT` | все рабочие слоты заняты |
| «Недостаточно ресурсов на узлах, ожидание…» | свободные слоты есть, но на их нодах не хватает памяти или CPU (`NODE_MEMORY_RESERVE_MB`, `NODE_GUEST_MEMORY_MB`, `NODE_CPU_OVERCOMMIT`) |
| «Proxmox недоступен, ожидание…» | менеджер не достучался до API Proxmox (`PVE_NODES`, токен) |
| `STUDENT_DIR_BUSY` | у какого-то слота в `slots` стоит `student_key` этого студента: §3, потом §4. Текст «Слот требует проверки администратором…» — слот в карантине: раздел `COMMAND_UNCERTAIN` |
| `SLOT_QUARANTINED` | агент держит отметку `uncertain` на слоте: раздел `COMMAND_UNCERTAIN` |
