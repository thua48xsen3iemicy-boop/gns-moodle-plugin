# devtools

| Что | Зачем |
|---|---|
| `build_zip.sh` | ZIP плагина для установки через интерфейс Moodle |
| `sync_demo_data.sh` | копирует `fixtures/ospf-multi-area.*` и `fixtures/linux-switch-vlan.*` в `plugin/demo` |
| `build_novnc.mjs` | собирает `plugin/amd/src/novnc.js` из пакета `@novnc/novnc` (esbuild, один ES-модуль); нужен только при смене версии noVNC, порядок команд — в шапке скрипта |
| `check_facts.mjs` | проверка чистых функций `facts.js` на Node, без Moodle и браузера (раздел «Проверка слоёв без браузера») |
| `mock-manager/mock_manager.py` | менеджер лаб в памяти (SPEC §3) и поддельная консоль Lab Slot |
| `ui-harness/` | интерфейс студента (`lab.php`) в браузере без Moodle: настоящие модули и шаблон, заглушки API Moodle, поддельный шлюз (`ui-harness/README.md`) |

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

   У активности эталонная лаба — `ospf-multi-area`: лабы mock-менеджера — это `fixtures/*.gns3`. Лаба `linux-switch-vlan`
   (два хоста и сервер с VLAN-подинтерфейсами вокруг Linux-коммутатора: access, транк без native, порт без VLAN) —
   то же с `labref` `linux-switch-vlan`: «Обновить сейчас» переключает верный снимок и снимок, где порт PC1 выключен
   (порт коммутатора на схеме тоже становится down).

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

## Живые факты лабы

Mock-менеджер ведёт себя как менеджер 0.8 (CONTRACTS §2.7): у запущенной сессии `features: ["consoles", "facts"]`,
`GET /api/v1/sessions/{id}/state` отдаёт `fixtures/ospf-multi-area.facts.json` с `node_id` живого проекта, `rev`,
`generated_at` и `ttl: 90`; с `since_rev`, равным `rev`, — `304`. Сбор «идёт» каждые 30 с: `generated_at` сдвигается,
`rev` — нет. `POST …/state/refresh` — `202` (у неживой сессии `409 session_not_running`), не чаще раза в 10 с: `rev`
растёт, снимок переключается между `facts.json` и `facts-broken.json`.

На странице лабы после запуска появляются слои L2 / L3 / OSPF, в «Обзоре» — «Факты собраны в …» и «Обновить сейчас»:
через ~5 с схема показывает конфиг с ошибками, следующее нажатие возвращает верный.

```bash
# сбор с R1 не удался: R1 бледнеет, в его карточке — «Устройство не ответило вовремя…»
curl -X POST localhost:8090/mock/facts/nodes/6b1f3c2e-4a7d-4e5b-9c10-000000000002/fail -d '{"code": "timeout"}'
curl -X POST localhost:8090/mock/facts/nodes/6b1f3c2e-4a7d-4e5b-9c10-000000000002/ok
# сбор встал: через ttl (90 с) факты помечаются «данные устарели», но показываются
curl -X POST localhost:8090/mock/facts/stall/on
curl -X POST localhost:8090/mock/facts/stall/off
```

Без Moodle то же видно в `ui-harness` (`?factsempty` — «нет данных», `?nofacts` — сессия без фактов,
`window.__stub.facts` — сбой ноды и устаревание).

## Проверка слоёв без браузера

```bash
node plugin/devtools/check_facts.mjs     # из корня репозитория; Node 22, зависимостей нет
```

Скрипт подключает настоящие `plugin/amd/src/model.js` и `facts.js` (префикс `mod_gns3lab/` ведёт в `amd/src`) и прогоняет
сценарии над маленькими лабами: девять сценариев задачи Б из `docs/linux-switch-integration/PLAN.md` (состояние обоих
концов линка и самого линка: порт коммутатора повторяет линк и не читает свои `admin` / `oper`) и режимы L2 `none` и
`native: null` (`docs/live-state.md` §3.1). Печатает `ok` / `FAIL` по каждому, при сбое выходит с кодом 1.

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
