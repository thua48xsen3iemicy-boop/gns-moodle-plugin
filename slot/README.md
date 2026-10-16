# Lab Slot — lifecycle prototype 0.3

Независимый исполнитель одной активной лаборатории внутри непривилегированного LXC:
Ubuntu 24.04, GNS3 **2.2.61**, Python, FastAPI, Uvicorn. Moodle и Proxmox не входят
в обязанности Slot. `manager/` и `plugin/` этим изменением не обновляются.

## Размещение и установка

Общий код, venv и unit: `/srv/gns3-ro/slot/` на RO CephFS. Рабочие проекты:
`/opt/gns3/projects/` на RW CephFS; образы: `/opt/gns3/images/{QEMU,IOU}`;
QGA: `/opt/gns3/qga/`. Общие образы никогда не изменяются.

На машине с RW-доступом к коду, Ubuntu 24.04 и той же архитектурой/Python:

```sh
cd /srv/gns3-ro/slot
python3 -m venv venv
venv/bin/python -m pip install -r requirements.txt
```

Venv собирается по тому же абсолютному пути, который видят LXC. Обновлять общий
релиз следует при остановленных сервисах, сохраняя предыдущий релиз для отката.
На каждом LXC (администратор; проверить параметры перед выполнением):

```sh
sudo install -d -m 0700 /etc/lab-slot
sudo cp /srv/gns3-ro/slot/slot.env.example /etc/lab-slot/slot.env
sudo sed -i "s/REPLACE_WITH_LONG_RANDOM_SECRET/$(openssl rand -hex 32)/" /etc/lab-slot/slot.env
sudo chmod 600 /etc/lab-slot/slot.env
sudo install -d -o gns3 -g gns3 -m 0700 /opt/gns3/qga /opt/gns3/projects/.slot-locks
sudo systemctl link /srv/gns3-ro/slot/lab-slot.service
sudo systemctl daemon-reload
sudo systemctl enable --now lab-slot.service
```

`StateDirectory=lab-slot` создаёт `/var/lib/lab-slot` с владельцем `gns3`.
Там хранятся SQLite, доверенный манифест, статусы и ключи идемпотентности.
Не удалять эту директорию между перезапусками. Не разделять её между LXC.
Переменные и токен находятся в `/etc/lab-slot/slot.env`, не на общей RO CephFS.
GNS3 API фиксирован: `http://127.0.0.1:3080/v2`. Uvicorn: loopback:8080,
**один worker**. Второй экземпляр с тем же state-dir блокируется файловой блокировкой.
Mount CephFS должен быть доступен до запуска сервиса.

## Жизненный цикл и гарантии

`deploy`: `DEPLOYING → STARTING → PROVISIONING → READY`. Ответ HTTP **202**
означает принятие операции. Успех определяется только её статусом `SUCCEEDED`.
Проверяются версия GNS3, состав нод и связей, фактический `started` всех устройств,
QGA Debian, serial-getty и доступность локальных Telnet-консолей. Ping между
учебными устройствами, VLAN, STP и административное состояние NIC не проверяются.

`auto_start` по умолчанию true; явное false отклоняется (422). При создании проекта
внутренний GNS3 auto_start выключен, чтобы порядок запуска контролировал Slot.

`stop`: `STOPPING → STOPPED` после подтверждения остановки всех нод.
`resume` использует прежний UUID и рабочие файлы, запускает и проверяет устройства.
Отсутствующий сохранённый проект вызывает `FAILED`, без пересоздания дисков.
Повторный deploy с прежним UUID не пересоздаёт ноды, диски или соединения.
Несовпадение сохранённого манифеста или топологии вызывает ошибку.

Команды `provisioning` выполняются только при первом создании проекта. При resume
проверяется serial-getty, но не повторяются первоначальные команды и enable.
Если первоначальная подготовка не завершилась либо была прервана, её результат
считается неопределённым: повторное исполнение и пропуск запрещены. Нужна ручная
диагностика/согласование; автоматического восстановления частично созданных
проектов и публичного маршрута принудительного подтверждения нет. Импорт ранее
неизвестного Slot проекта также не подтверждает выполненную преднастройку.

После рестарта незавершённые операции получают `FAILED / SERVICE_RESTART`.
Ранее READY лаборатории получают FAILED до явного resume: это позволяет заново
захватить блокировку и проверить реальность состояния. Автоматического replay
гостевых команд нет. Журнал операций сохраняется; автоматической очистки пока нет.

Монитор каждые `SLOT_MONITOR_INTERVAL` секунд проверяет ноды и служебную готовность.
Потеря ноды, QGA, getty или консоли переводит READY в FAILED. Проверки ограничены
`SLOT_READY_TIMEOUT`; обнаружение отказа служебного интерфейса может занять этот
таймаут дополнительно к интервалу. Автоперезапуска неисправных устройств нет.

Одновременно выполняется одна изменяющая операция. Конфликт возвращает 409.
Вторая лаборатория запрещена, пока первая не STOPPED; FAILED сохраняет владение.
Для повтора передавайте **Idempotency-Key**: тот же ключ и тело возвращают прежнюю
операцию с `replayed:true`, другое тело — 409. Если постусловие прежнего успеха
уже нарушено, возвращается `409 STALE_IDEMPOTENCY_KEY`. Новый намеренный запуск
требует нового ключа. Актуальное состояние находится в `laboratory.state`, отдельно
от исторического `status` операции.
Без ключа дедуплицируются только одновременно выполняющиеся идентичные запросы;
для deploy без UUID обязательно используйте ключ, иначе после завершения запроса
невозможно отличить новую лабораторию от повтора.

Блокировка UUID в `/opt/gns3/projects/.slot-locks` удерживается до STOPPED либо
завершения процесса. Все Slot должны видеть один RW-каталог блокировок, а CephFS
должна поддерживать согласованный flock. Это защита сотрудничающих Slot;
полноценные распределённые lease/fencing остаются задачей Manager. Также перед
запуском проверяется отсутствие другого запущенного проекта в локальном GNS3.
Не запускайте проекты вручную параллельно Slot.

## Преднастройка

В доверенном описании ноды можно задать:

```json
"provisioning": [
  {"command": "test -x /usr/bin/ip", "timeout_seconds": 20}
]
```

Для QEMU команды исполняются через QGA и `/bin/sh -c`; ненулевой exit code — FAILED.
Debian должен иметь установленный qemu-guest-agent. QGA и serial обязательны.
Для IOL команды — отдельные строки CLI, включая явные переходы режима (`enable`,
`configure terminal`, `end`), если они нужны. `cli_prompt` — регулярное выражение
ожидаемого prompt, по умолчанию `(?m)^[^\r\n]+[>#]\s*$`. Используйте точный prompt
своего образа. Адаптер обрабатывает Telnet negotiation, ждёт prompt после каждой
команды, распознаёт стандартные Cisco `% Invalid/Error/Incomplete/Ambiguous/Unknown`.
Авторизация, интерактивные подтверждения и нестандартные ошибки требуют отдельного
адаптера: текущий механизм не подтверждает семантическую корректность конфигурации.
Без provisioning проверяется только TCP-доступность CLI, не интерактивный login.
Время ожидания начального CLI prompt — 30 секунд, каждой команды — её timeout.

## API и ручная проверка GNS3 2.2.61

Ниже команды выполняют реальные операции только при запуске оператором.
Пример `examples/cisco-3x2.json`: три Cisco в кольце и шесть Debian.
Сначала проверьте наличие образов и достаточную RAM: теперь запускаются все ноды.

```sh
cd /srv/gns3-ro/slot
TOKEN=$(sudo sed -n 's/^SLOT_API_TOKEN=//p' /etc/lab-slot/slot.env)
BASE=http://127.0.0.1:8080
curl -fsS http://127.0.0.1:3080/v2/version
curl -fsS "$BASE/healthz"
curl -fsS -H "Authorization: Bearer $TOKEN" "$BASE/v1/readyz"

# Сохраните project_id и operation_id из ответа; повтор с этим ключом безопасен.
curl -sS -X POST "$BASE/v1/labs/deploy" \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: lab-demo-deploy-001' \
  --data-binary @examples/cisco-3x2.json
PID=PROJECT_UUID_FROM_RESPONSE
OP=OPERATION_UUID_FROM_RESPONSE
curl -sS -H "Authorization: Bearer $TOKEN" "$BASE/v1/operations/$OP"
curl -sS -H "Authorization: Bearer $TOKEN" "$BASE/v1/labs/$PID/status"
curl -sS -H "Authorization: Bearer $TOKEN" "$BASE/v1/labs/$PID/topology"

curl -sS -X POST -H "Authorization: Bearer $TOKEN" \
  -H 'Idempotency-Key: lab-demo-stop-001' "$BASE/v1/labs/$PID/stop"
# Дождитесь SUCCEEDED новой operation_id, затем:
curl -sS -X POST -H "Authorization: Bearer $TOKEN" \
  -H 'Idempotency-Key: lab-demo-resume-001' "$BASE/v1/labs/$PID/resume"
# Дождитесь SUCCEEDED; UUID нод, связи и рабочие данные должны сохраниться.
NODE=NODE_UUID_FROM_TOPOLOGY
curl -sS -X POST "$BASE/v1/labs/$PID/nodes/$NODE/wipe" \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: lab-demo-wipe-001' -d '{"confirm":true}'
# Polling вернёт NOT_SUPPORTED; устройства и файлы остаются нетронутыми.
```

Сохранены `GET /v1/labs`, `GET /v1/labs/{project_id}`, topology, node console,
`POST .../qga/exec`, `POST .../serial/enable` и WS `/v1/console/{project_id}/{node_id}`.
Отдельные публичные node start/stop, lab start/open/close удалены: используйте
deploy/resume/stop для всей лаборатории. WebSocket авторизуется субпротоколами
`['lab-slot', 'bearer.' + token]`; данные двоичные.

Bearer API предназначен доверенному Manager. QGA даёт выполнение команд в госте;
не выдавайте токен студентам. Для удалённого доступа нужен защищённый шлюз/mTLS;
защитите также `/docs` и `/openapi.json`.

## Node Wipe: намеренный отказ от неподтверждённого удаления

Маршрут требует `confirm:true`, проверяет принадлежность ноды проекту и возвращает
асинхронный **NOT_SUPPORTED**. Ни нода, ни её соседи не останавливаются, файлы не
меняются. Лаборатория возвращается в предыдущее состояние. Успешный wipe в этой
версии не реализован, вместо него нет имитации успеха.

Исследованы исходники upstream тега v2.2.61:

- [QemuVM](https://github.com/GNS3/gns3-server/blob/v2.2.61/gns3server/compute/qemu/qemu_vm.py):
  `_disk_options` создаёт `*_disk.qcow2` в working_dir для linked clone; присутствуют
  отдельные ветви создания/rebase и дополнительные накопители.
- [IOUVM](https://github.com/GNS3/gns3-server/blob/v2.2.61/gns3server/compute/iou/iou_vm.py):
  `_nvram_file`, `_push_configs_to_nvram`, `_rename_nvram_file` связывают NVRAM,
  startup/private config и vlan.dat с application_id; работает file watcher.

Знания имён недостаточно: пока не подтверждены полный список изменяемых файлов,
отсутствие открытых дескрипторов, правильный эталон и атомарное восстановление.
Поэтому Slot не удаляет overlays или NVRAM вслепую. Следующий адаптер должен
подтвердить эти условия и сохранить UUID/имя/координаты/соединения и соседние ноды.

## Автоматические проверки и пределы проверки

Из корня репозитория:

```sh
python3 -m venv /tmp/lab-slot-test-venv
/tmp/lab-slot-test-venv/bin/python -m pip install -r slot/requirements.txt pytest
PYTHONPATH=slot /tmp/lab-slot-test-venv/bin/python -m pytest -q slot/tests
python3 -m compileall -q slot
python3 slot/release.py check
```

Тесты используют подменённый HTTP GNS3, временные SQLite/блокировки и локальные
проверки служебных адаптеров. Они не подтверждают интеграцию с настоящим GNS3,
QGA гостевых VM, реальным Cisco CLI, CephFS flock, systemd/LXC, сохранность реальных
QCOW2 или runtime-производительность. Проверка этих свойств требует стенда.
L1-sidecar, автоматическая оценка задания, reset файлов и UI не реализованы.

## Исправления по итогам интеграционных испытаний, версия 0.3.0

По предоставленным результатам испытаний исходный цикл на GNS3 2.2.61 прошёл:
Deploy → READY (3 Cisco + 6 Debian) → Stop → STOPPED → Resume → READY,
QGA и сохранность `/root/slot-test.txt` (`SLOT_PERSISTENCE_OK`) после Stop/Resume
и перезагрузки LXC. Это результаты испытаний предыдущего жизненного цикла,
предоставленные оператором, а не проверка текущего обновления в этой среде.
Основной порядок запуска, QGA/CLI/WS и сохранение QCOW2 не переписывались.

### Готовность и ошибки

`/healthz` означает только живой процесс; содержит версию. `/v1/readyz` возвращает
200 `ready` или 503 `not_ready`: `version`, `build_id`, `gns3_version`, `checks`.
Проверяются Lifecycle/monitor, транзакционная запись SQLite с rollback, временная
запись/fsync во внутреннее хранилище, временный flock в каталоге блокировок,
локальный GNS3 2.2.61 и manifest текущего релиза. Для сетевых и файловых проверок
readiness установлен таймаут 3 секунды на проверку; SQLite использует busy timeout
1 секунду. Это не жёсткое ограничение зависания ядра/диска: проблемный CephFS mount
требует диагностики ОС. Проверки не запускают и не останавливают VM, не меняют
проектные файлы. Заранее создайте каталог блокировок с владельцем `gns3`.

Startup отказывается запускаться при нарушении manifest. Перед любыми изменяющими
HTTP-запросами повторно проверяются manifest и исходная build identity; неполный
Lifecycle или изменённый релиз дают 503 с диагностическим кодом. Ошибки SQLite и
файловой системы преобразуются в `STATE_STORAGE_ERROR` / `FILESYSTEM_ERROR`,
без содержимого секретов. Конфликт с текущей операцией — `OPERATION_IN_PROGRESS`.
`FAILED` требует проверки status/операции; проект автоматически не пересоздаётся,
QCOW2 не удаляются и первоначальная преднастройка не повторяется.
Исправлено восстановление после сбоя между сохранением STOPPED и завершением
операции: сервис поднимается с диагностикой FAILED вместо ошибки перехода FSM.
Начальное состояние и запись принятой операции сохраняются одной транзакцией;
при её отказе новая файловая блокировка освобождается.

POST deploy/resume/stop/wipe возвращает компактные `operation_id`, `project_id`,
`action`, `status`, `replayed`, `laboratory`. GET operation добавляет `stage`,
`progress`, `error`, `updated_at` и `nodes` только с UUID/именем/типом/статусом.
Полные параметры остаются в GET lab/topology и node console; команды QEMU не
дублируются в ответах операций. Поле `nodes` сохранено, его элементы стали сводкой:
клиентам, читавшим properties из операции, следует использовать topology.

Одинаковый Idempotency-Key и параметры во время RUNNING возвращают ту же операцию.
После завершения ответ содержит `replayed:true` и актуальную лабораторию.
Успешный deploy/resume имеет постусловие READY, stop — STOPPED. Если оно уже
нарушено (в том числе после рестарта с FAILED), ответ — 409 с
`detail.code=STALE_IDEMPOTENCY_KEY`, прежним operation_id и актуальным состоянием.
Новая операция не запускается. Другие параметры с тем же ключом —
`IDEMPOTENCY_KEY_CONFLICT`. Новое намеренное действие требует нового ключа.
Исторический GET operation остаётся доступен даже при изменении состояния.
Эти правила действуют и для ключей, сохранённых предыдущей версией SQLite.

### Безопасное обновление общей CephFS

Нельзя копировать файлы поверх используемого релиза. Новый релиз создаётся в
окончательном физическом каталоге, например
`/srv/gns3-ro/releases/<BUILD>/slot`; общий `/srv/gns3-ro/slot` становится ссылкой
на него. Каждый релиз имеет **свой venv**, собранный сразу по этому физическому
пути. Не переносите готовый venv из старого пути и не копируйте старые console
scripts. Все LXC сохраняют RO CephFS и прежнюю публичную точку `/srv/gns3-ro/slot`.

Обновление контролируемое: сначала подготовить и проверить новый каталог,
потом остановить **все lab-slot.service, использующие общую ссылку**, переключить
ссылку и запустить сервисы. GNS3 и VM при этом можно оставить работающими;
кратковременно недоступны API/WS. После старта ранее READY получает FAILED до
явного Resume с **новым** ключом. SQLite и `/opt/gns3/projects` не переносятся.
Cold Resume между разными LXC здесь не реализуется.

Команды на RW-узле (BUNDLE — подготовленный каталог `slot/` выбранного Git-коммита;
BUILD задаётся оператором, например полный commit SHA). Команды не выполняются
приложением автоматически:

```sh
set -eu
BUILD=REPLACE_WITH_SOURCE_COMMIT_SHA
BUNDLE=/path/to/checked-out/gns-moodle-plugin/slot
NEW=/srv/gns3-ro/releases/$BUILD/slot
# Новый уникальный путь; mkdir откажется перезаписывать существующий релиз.
mkdir -p "/srv/gns3-ro/releases/$BUILD"
mkdir "$NEW"
cp -a "$BUNDLE/." "$NEW/"
# В BUNDLE не должно быть venv или runtime-данных; не копируйте рабочий каталог LXC.
python3.12 -m venv "$NEW/venv"
"$NEW/venv/bin/python" -m pip install -r "$NEW/requirements.txt"
"$NEW/venv/bin/python" -m pip check
"$NEW/venv/bin/python" "$NEW/release.py" seal --build-id "$BUILD"
"$NEW/venv/bin/python" "$NEW/release.py" check
```

Manifest SHA256 включает Python, unit, requirements, примеры и документацию.
`release.py check` проверяет обязательные файлы, Python 3.12, диапазоны версий
зависимостей и импорт компонентов. `build_id` — заданный source SHA или автоматически
вычисленная идентичность содержимого. Это обнаружение смешанных/повреждённых файлов,
не криптографическая подпись поставщика. `seal` разрешён только при сборке нового
каталога; никогда не пересоздавайте manifest для сокрытия ошибки работающего релиза.
После seal каталог считается неизменяемым. Изменили код в разработке — запустите
seal заново перед тестами и check; исходный `release.json` включён в репозиторий.

**На каждом LXC из полного списка использующих общий путь**:

```sh
sudo systemctl stop lab-slot.service
sudo systemctl is-active lab-slot.service  # ожидается inactive; exit code ненулевой
```

Оператор обязан дождаться остановки на всём списке. Скрипт не обнаруживает LXC
автоматически: флаг ниже — явное подтверждение выполненной координации, не её имитация.
На первом переходе, если slot ещё обычный каталог, только после этой остановки
на RW-узле переименуйте его в резервный путь (пример: `/srv/gns3-ro/slot-legacy`).
Старый venv после переименования не запускайте: при откате сначала верните весь
каталог на исходный путь. Для последующих обновлений достаточно переключения ссылки:

```sh
# Только при первой миграции обычного каталога, после остановки всех API:
# mv /srv/gns3-ro/slot /srv/gns3-ro/slot-legacy
"$NEW/venv/bin/python" "$NEW/tools/activate_release.py" "$NEW" --all-services-stopped
```

Скрипт откажется заменять обычный каталог, проверит релиз и pip check, затем атомарно
заменит ссылку в том же CephFS-каталоге. `run.py` фиксирует физический путь импортов
и проверяет соответствие venv; unit выполняет preflight до запуска Uvicorn.
На каждом остановленном LXC обновите локальную копию unit (это также устраняет
старую systemd-ссылку на unit из другого релиза), проверьте новый релиз и запустите:

```sh
sudo /srv/gns3-ro/slot/venv/bin/python /srv/gns3-ro/slot/release.py check
sudo ln -sfn /srv/gns3-ro/slot/lab-slot.service /etc/systemd/system/lab-slot.service
sudo systemctl daemon-reload
sudo systemctl start lab-slot.service
sudo systemctl status lab-slot.service
```

Используйте `tools/check_slot.py` ниже перед возобновлением лаборатории. При отказе
не удаляйте данные: остановите все Slot API и той же процедурой верните ссылку на
предыдущий проверенный физический релиз, затем обновите unit и запустите сервисы.
Предыдущий релиз должен поддерживать ту же SQLite-схему; эта версия не меняет схему
таблицы records. Откат самого первого legacy-каталога требует восстановления его
исходного абсолютного пути при остановленных API. Не переключать релиз для части
работающих LXC: общий alias действует на весь парк.

### Короткая повторная интеграционная проверка

Диагностический режим не создаёт проекты, проверяет существующий JSON до HTTP,
версию API и readiness. Он не зависит от файлов `/tmp`. Передайте токен через
переменную окружения (не аргумент CLI и не Git):

```sh
cd /srv/gns3-ro/slot
export SLOT_API_TOKEN  # установите из защищённой конфигурации LXC
venv/bin/python tools/check_slot.py
# Только по явному решению оператора: создание/запуск эталонной лаборатории.
venv/bin/python tools/check_slot.py --deploy --key integration-deploy-NEW001
```

1. Сохранить PID/OP из ответа, polling GET operations до SUCCEEDED, status READY,
   topology — девять started; до этого не считать deploy успешным.
2. QGA создать/прочитать контрольный файл на одной Debian, как в прежних испытаниях.
3. POST stop с новым ключом, дождаться STOPPED/девяти stopped.
4. POST resume с ключом `integration-resume-NEW001`, дождаться READY; прочитать файл.
5. Повторить тот же Resume — тот же OP, `replayed:true`, `laboratory.state=READY`.
6. Stop с новым ключом; старый Resume-ключ должен дать 409 STALE_IDEMPOTENCY_KEY,
   лаборатория остаётся STOPPED. Новый Resume-ключ снова запускает её.
7. В согласованное окно перезапустить LXC; проверить SQLite/readiness, Resume с новым
   ключом и содержимое контрольного файла. Старые записи операций остаются доступны.

Ни проверочный CLI, ни процедура обновления не удаляют проекты. Автоматические
тесты покрывают mock GNS3, повреждение релиза, replay/stale, SQLite и блокировки.
Ручной проверки требуют CephFS/флот LXC/права/venv/systemd и повтор основного цикла
с реальными девятью устройствами и QCOW2. Безопасный Node Wipe по-прежнему отдельно.
