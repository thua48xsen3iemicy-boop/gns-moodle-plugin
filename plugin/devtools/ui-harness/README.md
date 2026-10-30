# ui-harness — интерфейс студента без Moodle

Открывает страницу студента (`lab.php`) в браузере, не поднимая Moodle, менеджер и шлюз. Для правок интерфейса: схема,
колесо действий, сведения, консоли, телефонная ширина, тёмная тема.

Что настоящее: модули `plugin/amd/src` (собираются Babel в AMD), шаблон `plugin/templates/viewer.mustache`
(рисуется настоящим Mustache со строками из `plugin/lang`), `plugin/styles.css`, демо-лаба `plugin/demo`.
Что подделано: `core/ajax` (сессия в памяти: старт → запуск → работает, остановка), `core/str`, `core/notification`
(`stubs/`) и шлюз консолей (`server.js`, WebSocket `/gw/console/<node_id>`): эхо ввода и приглашение по виду устройства — Cisco IOS
(IOU, маршрутизаторы) `R1#`, VPCS `PC1> `, остальные узлы — Linux `user@pc1:~$ `; `reload` закрывает консоль кодом 4000.
Ответы подобраны так, чтобы слой «увидено в консоли» (`docs/live-state.md` §8) было что показать сразу: `show ip interface
brief`, `show interfaces status` (порт Et0/2 коммутатора выключен), `show vlan brief` (есть VLAN 20, которого нет в фактах),
`write memory` / `wr` с `[OK]`, у Linux `ip a`, `ip -br a`, `ip r` и у VPCS `show ip` с другим адресом, чем в фактах.

```bash
cd plugin/devtools/ui-harness
npm install
node build.js          # → dist/
node server.js         # → http://127.0.0.1:8123/index.ru.html
```

Страницы: `index.ru.html`, `index.en.html`, `index.viewer.html` (пользователь без права запуска); режимы схемы (`docs/lab-constructor/README.md`
§2) — `index.<язык>.seen.html`, `index.<язык>.fog.html` (все устройства силуэтами) и `index.<язык>.fog-start.html` (туман со стартовым
PC1); с `?observer` те же страницы открывает преподаватель: он видит всё и по кнопке «Как видит студент» — маску студента; лаба `linux-switch-vlan`
(Linux-коммутатор с VLAN и транком; «Обновить сейчас» выключает порт PC1) — `index.ru.linux-switch-vlan.html` и
`index.en.linux-switch-vlan.html`, остальные параметры те же. Параметры адреса:
`?fast` — лаба запускается за секунду, `?theme=dark`, `?min` — модули из `plugin/amd/build`, `?conflict` — старт отвечает
«уже есть другая лаба», `?observer` — чужая работающая лаба, только просмотр, `?nofacts` — сессия без фактов,
`?factsempty` — факты ещё не собраны (rev 0). В консоли браузера `window.__stub.setNode(<node_id>, 'stopped'|'started')` меняет состояние ноды. Живые факты — демо-фикстуры: «Обновить сейчас» переключает верный конфиг и конфиг с ошибками;
`window.__stub.calls` считает вызовы external functions (по имени), по нему видно, спрашивала ли страница факты;
`__stub.facts.fail(<node_id>, 'timeout')` — сбор с ноды не удался, `__stub.facts.stale(true)` — сбор встал давно,
`__stub.facts.fill()` / `empty()` — есть данные / нет данных. Опрос идёт раз в 30 с; событие `visibilitychange` на
`document` запрашивает факты сразу.

`node build.js --amd-build [--only=lab,stage]` пишет минифицированные модули и карты в `plugin/amd/build` — запасной
вариант на случай, когда `grunt amd` недоступен. Это не замена: перед выпуском пересоберите `grunt amd` в dev-Moodle
(ESLint Moodle, родная сборка), результат Babel и terser от неё отличается.

## Графическая консоль (noVNC)

Нода с `console_type: vnc` (в демо-лабе — PVE1) открывается экраном. Шлюз харнесса для неё не отвечает сам, а пересылает
двоичные кадры (сырой поток RFB) в обе стороны поддельному слоту mock-менеджера (`HARNESS_SLOT_URL`, по умолчанию
`ws://127.0.0.1:8091`) и закрывает консоль кодом `4000` (слот закрылся) или `4001` (слот не пустил). Значит, для экрана
нужен запущенный `devtools/mock-manager/mock_manager.py` (`pip install aiohttp`); без него консоль PVE1 уходит в
«Консоль устройства недоступна». `GET /gw/stats` — сколько экранов открыто на шлюзе сейчас.

Сквозная проверка в Chromium — `check_vnc.js`: поднимает оба сервера на портах 18090, 18091 и 18123, ведёт страницу
(рабочий стол, телефон, преподаватель) и читает, что дошло до RFB-сервера, из `GET /mock/vnc/<node_id>`:

```bash
cd plugin/devtools/ui-harness
npm install
PYTHON=python3 node check_vnc.js --shots /tmp/vnc-shots   # нужен Playwright с Chromium (require('playwright'))
```

Каждая проверка печатается как `PASS` / `FAIL`, код выхода — число провалов; скриншоты (панель после ввода, сетка, окна,
пауза переподключения, шторка телефона) — в каталоге `--shots` (по умолчанию `dist/shots`).

Страница преподавателя «Лабы курса» (`course.php`, `docs/teacher-dashboard.md`): `index.<язык>.course.html` — настоящий шаблон `course.mustache`
и модуль `course.js` против заглушки `mod_gns3lab_get_course_overview` (пять студентов, две лабы, две группы; у студентов 11 и 12 есть факты — верные и с
ошибками). `?laberror` — менеджер не ответил по второй лабе, `?coursefail` — вызов не удался. `window.__stub.course.set(cmid, userid, сводка|null)` меняет ячейку,
`.bump(userid)` двигает rev фактов. Сквозная проверка в Chromium — `check_course.js` (таблица, цифры в ячейках, «без изменений N мин» на часах Playwright, поиск,
сортировка, группа, карточка и её адрес, телефон 360 px, ошибка по лабе, экранирование имён): `node check_course.js [--min] [--shots <каталог>]`.

Одно место на лабу (`docs/seat.md`): `index.<язык>.seat.html` — активность в режиме «одна страница»; `?seat=othertab` и
`?seat=otherlogin` открывают её с местом, занятым другой страницей того же входа или другим входом; в консоли браузера
`window.__stub.seat.give('me'|'othertab'|'otherlogin')` передаёт место, `__stub.seat.claims` и `.gated` показывают, что и с каким
id страницы страница спрашивала. Сквозная проверка — `check_seat.js` (порт 18127, Playwright с Chromium; `--min` — минифицированные
модули): стена для другого входа и для другой вкладки, кнопка «Работать с лабой здесь», закрытие консолей, телефон, русский текст.

```bash
cd plugin/devtools/ui-harness
node check_seat.js [--min] [--shots /tmp/seat-shots]
```

Проверка слоя «увидено в консоли» — `check_seen.js`: поднимает только шлюз харнесса (порт 18124), в Chromium запускает
лабу, набирает в консолях SW1 и PC1 `sh int status`, `sh vl br`, `show ip`, `wr` и проверяет, что схема меняется сразу, без
опроса фактов (кабель к PC2 гаснет, адрес PC1 на L3 меняется), что карточка узла подписана и поля отмечены, что `write` с `[OK]`
просит свежие факты не чаще раза в 10 с и что новый снимок сбрасывает увиденное на SW1, но не на PC1. `--min` — те же
проверки на минифицированных модулях `amd/build`.

```bash
cd plugin/devtools/ui-harness
npm install
node check_seen.js [--min] [--shots /tmp/seen-shots]    # нужен Playwright с Chromium; mock-менеджер не нужен
```

Редактор лаб в харнессе: `editor.<язык>.html` (менеджер с конструктором: панель «Сегменты», предпросмотр), `editor.<язык>.plain.html`
(менеджер без него) и `editor.<язык>.segments.html` (лаба, в которой уже есть сегмент). Заглушки `validate`, `save` и `publish` в
`stubs/ajax.js` отказывают на повторе номера адреса, плохой подсети и пустом сегменте и предупреждают о каждом шлюзе и VLAN;
последнее сохранённое определение — `window.__stub.editor.saved`. Сквозная проверка — `check_editor.js` (порт 18126, Playwright
с Chromium; `--min` — минифицированные модули):

```bash
cd plugin/devtools/ui-harness
npm install
node check_editor.js [--min] [--shots /tmp/editor-shots]
```

Проверка режимов `seen` и `fog` — `check_reveal.js`: поднимает шлюз харнесса (порт 18125), в Chromium ведёт страницы `fog`, `fog-start`,
`seen` и преподавателя: силуэты и открытие устройства первым выводом, интерфейс и кабель, сосед, узел без консоли, выключенный и затем
поднятый интерфейс (`__stub.facts.patch`), перезагрузка страницы (маска живёт в `sessionStorage` вкладки: заглушки `get_reveal` и
`reveal` повторяют проверки сервера), стартовые узлы, карточки с «ещё не исследовано», переключатель преподавателя. `--min` — те же
проверки на минифицированных модулях `amd/build`.

```bash
cd plugin/devtools/ui-harness
npm install
node check_reveal.js [--min] [--shots /tmp/reveal-shots]    # нужен Playwright с Chromium; mock-менеджер не нужен
```

Не проверяет: PHP, права, настоящих менеджер и шлюз, тему Boost (в `index.template.html` — лишь несколько переменных
Bootstrap), поведение клавиатуры телефона.
