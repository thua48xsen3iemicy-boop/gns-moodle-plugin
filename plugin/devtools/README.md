# devtools

| Что | Зачем |
|---|---|
| `build_zip.sh` | ZIP плагина для установки через интерфейс Moodle |
| `sync_demo_data.sh` | копирует `fixtures/ospf-multi-area.*` в демо-лабу плагина |
| `mock-manager/mock_manager.py` | менеджер лаб в памяти (SPEC §3) и поддельная консоль Lab Slot |

## Плагин с консолями без Proxmox

Нужны Python 3.10+ и `aiohttp` (`pip install aiohttp`). Три процесса: шлюз, mock-менеджер и Moodle.

```bash
# 1. Шлюз консолей — код менеджера (manager/gateway/, ветка claude/determined-brown-lfgnxy).
#    Без checkout ветки менеджера достаточно одного файла:
git show origin/claude/determined-brown-lfgnxy:manager/gateway/console_gateway.py > /tmp/console_gateway.py
#    Билеты от mock-менеджера, консоли — у поддельного слота на :8091
GW_PORT=8080 GW_INTERNAL_KEY=dev-internal-key \
GW_SLOTS='{"127.0.0.1": "dev-console-token"}' GW_SLOT_PORT=8091 \
GW_ALLOWED_ORIGINS=http://127.0.0.1:8000 python3 /tmp/console_gateway.py

# 2. Mock-менеджер: API на :8090, поддельный Lab Slot на :8091 (значения по умолчанию совпадают со шлюзом выше)
python3 devtools/mock-manager/mock_manager.py
```

3. В Moodle («Администрирование → Плагины → Модули деятельности → Лаба GNS3»):
   - адрес менеджера — `http://127.0.0.1:8090`;
   - секрет — `dev-secret`;
   - адрес шлюза можно не задавать: mock отдаёт `ws://127.0.0.1:8080` вместе с билетом.

   У активности эталонная лаба — `ospf-multi-area`: лабы mock-менеджера — это `fixtures/*.gns3`.

`GW_ALLOWED_ORIGINS` — origin сайта Moodle, с которого браузер открывает консоли. Настройка обязательна: без неё
шлюз не запускается.

После этого студент нажимает «Запустить лабу». Через ~5 с лаба запущена. Клик по R1 → «Открыть консоль» →
приглашение `R1#`, на ввод отвечает эхо.

Ручные проверки:

```bash
# нода остановлена: консоль закроется (4000), на странице «Нода остановлена»
curl -X POST localhost:8090/mock/nodes/6b1f3c2e-4a7d-4e5b-9c10-000000000002/stop
# нода снова работает: консоль подключится сама
curl -X POST localhost:8090/mock/nodes/6b1f3c2e-4a7d-4e5b-9c10-000000000002/start
```

Команда `reload` в консоли закрывает её на стороне слота: так проверяется переподключение. Поддельная консоль
понимает режимы IOS (`enable`, `configure terminal`, `interface …`, `router …`, `exit`, `end`, Ctrl+Z, Ctrl+U) и меняет
приглашение, как устройство: на этом проверяется клавиатура консоли.

```bash
# следующий запуск упадёт с этим кодом (retryable: false — «Сообщите преподавателю код: …»)
curl -X POST localhost:8090/mock/fail-next -d '{"code": "AGENT_REJECTED", "retryable": false}'
# статусы нод «устарели» (nodes_stale): закрытие консоли не значит «нода остановлена»
curl -X POST localhost:8090/mock/stale/on
curl -X POST localhost:8090/mock/stale/off
```

## Библиотека и редактор лаб

Mock-менеджер ведёт себя как менеджер 0.5 (CONTRACTS §2.6, ветка `claude/determined-brown-lfgnxy`): объявляет
`images`, `lab_definitions` и `lab_formats`, отдаёт каталог образов (`GET /api/v1/images`, пять образов: IOU L3/L2,
Debian, Debian-сервер, VPCS), проверяет лабу (`POST /api/v1/labs/validate` — перенос `manager/lab_manager/labdef.py`:
те же правила, коды замечаний и размер CT) и запускает сессию по `lab`. Коды сбоев менеджера проверяются через
`/mock/fail-next`, например `{"code": "NO_SLOTS", "retryable": false}`.

1. Назначьте пользователю роль «Автор лаб» на уровне сайта («Администрирование → Пользователи → Права → Назначить
   системные роли»). У него появится пункт «Библиотека лаб» в главном меню; администратор найдёт библиотеку и в
   разделе плагина.
2. «Новая лаба» → название и метки → редактор. Устройства — из списка слева, «Соединить» — два устройства по очереди,
   «Проверить» и «Опубликовать» спрашивают mock.
3. В настройках активности выберите опубликованную версию в поле «Лаба» — студент запустит именно её.

Работа студента сохраняется, как у менеджера 0.5.1: `resume` всегда есть в `info`, повторный запуск продолжает тот же
проект. Поддельная консоль отвечает на `write memory` строкой `[OK]`. `MOCK_RESUME=0` — старое поведение без
сохранения: остановка спрашивает подтверждение.

Переменные mock-менеджера (`MOCK_PORT`, `MOCK_SLOT_PORT`, `MOCK_SECRET`, `MOCK_CONSOLE_TOKEN`, `MOCK_RESUME`, `GW_INTERNAL_URL`,
`GW_INTERNAL_KEY`, `GW_PUBLIC_URL`, `MOCK_FIXTURES`) заданы в начале `mock_manager.py`, там же значения по умолчанию.
