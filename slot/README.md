# Lab Slot — lifecycle prototype 0.2

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
операцию, другое тело — 409. Новый намеренный запуск требует нового ключа.
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
```

Тесты используют подменённый HTTP GNS3, временные SQLite/блокировки и локальные
проверки служебных адаптеров. Они не подтверждают интеграцию с настоящим GNS3,
QGA гостевых VM, реальным Cisco CLI, CephFS flock, systemd/LXC, сохранность реальных
QCOW2 или runtime-производительность. Проверка этих свойств требует стенда.
L1-sidecar, автоматическая оценка задания, reset файлов и UI не реализованы.
