# ui-harness — интерфейс студента без Moodle

Открывает страницу студента (`lab.php`) в браузере, не поднимая Moodle, менеджер и шлюз. Для правок интерфейса: схема,
колесо действий, сведения, консоли, телефонная ширина, тёмная тема.

Что настоящее: модули `plugin/amd/src` (собираются Babel в AMD), шаблон `plugin/templates/viewer.mustache`
(рисуется настоящим Mustache со строками из `plugin/lang`), `plugin/styles.css`, демо-лаба `plugin/demo`.
Что подделано: `core/ajax` (сессия в памяти: старт → запуск → работает, остановка), `core/str`, `core/notification`
(`stubs/`) и шлюз консолей (`server.js`, WebSocket `/gw/console/<node_id>`): приглашение `R1#`, эхо ввода,
`show ip interface brief`, `write memory`, `reload` закрывает консоль кодом 4000.

```bash
cd plugin/devtools/ui-harness
npm install
node build.js          # → dist/
node server.js         # → http://127.0.0.1:8123/index.ru.html
```

Страницы: `index.ru.html`, `index.en.html`, `index.viewer.html` (пользователь без права запуска). Параметры адреса:
`?fast` — лаба запускается за секунду, `?theme=dark`, `?min` — модули из `plugin/amd/build`, `?conflict` — старт отвечает
«уже есть другая лаба», `?observer` — чужая работающая лаба, только просмотр. В консоли браузера `window.__stub.setNode(<node_id>, 'stopped'|'started')` меняет состояние ноды.

`node build.js --amd-build [--only=lab,stage]` пишет минифицированные модули и карты в `plugin/amd/build` — запасной
вариант на случай, когда `grunt amd` недоступен. Это не замена: перед выпуском пересоберите `grunt amd` в dev-Moodle
(ESLint Moodle, родная сборка), результат Babel и terser от неё отличается.

Не проверяет: PHP, права, настоящих менеджер и шлюз, тему Boost (в `index.template.html` — лишь несколько переменных
Bootstrap), поведение клавиатуры телефона.
