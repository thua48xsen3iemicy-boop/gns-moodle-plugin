# План: живые факты лабы в плагине (0.6.0)

Менеджер 0.8.0 хранит факты о запущенной лабе и отдаёт их маршрутом `/sessions/{id}/state`
(`manager/docs/CONTRACTS.md` §2.7, формат — `docs/live-state.md`). В плагине правила слоёв уже есть
(`amd/src/facts.js`), но факты берутся только из демо-фикстур. Задача — показывать на схеме живые факты сессии.

## Что уже есть

- `facts.js` — `derive(model, factsDoc)` → L1/L2/L3/OSPF и проблемы; `stage.js` принимает `facts: {ok, broken}` (демо);
  `details.js` пишет «Факты из лабы: … · демо».
- `get_state` (external) отдаёт сессию; `session_data` не передаёт `features` сессии.
- `manager_client` без маршрутов `state`. В mock-менеджере `state` нет.

## Шаги

1. **`manager_client`** (`classes/local/manager_client.php`):
   - `get_session_facts(string $sessionid, ?int $sincerev = null): ?\stdClass` — `GET /api/v1/sessions/{id}/state[?since_rev=N]`;
     `304` → `null` (ничего не изменилось). Проверить, что `request()` умеет 304 без тела; если нет — доработать.
   - `refresh_session_facts(string $sessionid): void` — `POST …/state/refresh`, `202`; `409 session_not_running` — исключение как у остальных.
   - Проверка конверта: `schema === 'gns3lab.facts/1'`, иначе ошибка «неподдерживаемый формат»; `nodes` — объект.
2. **`session_data`**: добавить `features` (список строк из `session.features`, пустой, если нет).
   Плагин читает факты, только если в нём есть `facts` (CONTRACTS §7).
3. **MUC-кэш** `facts` в `db/caches.php` (application, simplekeys, ключ — `sessionid`): `{rev, doc (JSON-строка), fetchedat}`.
   Свежая запись (моложе 15 с) отдаётся без запроса к менеджеру; старше — запрос с `since_rev=rev`, `304` продлевает запись.
4. **External functions** (`db/services.php`, `ajax => true`, `loginrequired => true`):
   - `mod_gns3lab_get_facts(cmid, userid = 0, sincerev = 0)` → `{available, changed, rev, facts (PARAM_RAW, JSON), }`.
     Порядок проверок как в `get_state`: `validate_parameters` → `validate_context` → `require_capability('mod/gns3lab:view')`
     → `lab_access::resolve_user()` (свои или наблюдение по группам). Сессию ищет сам сервер (`find_session`), sessionid
     из браузера не принимается. Нет сессии или нет `facts` в `features` → `available: false`.
     `sincerev` из браузера сравнивается с кэшем: совпало → `changed: false`, без тела.
   - `mod_gns3lab_refresh_facts(cmid, userid = 0)` — те же проверки, только для `running`-сессии; вызывает `refresh_session_facts`
     и сбрасывает запись кэша. Частоту ограничивает менеджер (10 с), плагину отдельный лимит не нужен.
5. **JS**:
   - `lab.js`: пока загружена живая топология (`loadLive`) и в `session.features` есть `facts` — опрос `get_facts` раз в 30 с,
     только когда страница видна (`visibilitychange`), с `sincerev`. Первый запрос — сразу после `loadLive`.
     Смена сессии/остановка — опрос прекращается, схема возвращается к превью (демо-факты превью не трогать).
   - `stage.js`: метод `setFacts(doc)` для живого режима — без пересоздания модели и без сброса вида; выделение,
     которого больше нет, снимается (логика уже есть в `setFactsKey`, вынести в общую функцию). Переключатель
     «ок/с ошибками» в живом режиме скрыт. Слои L2/L3/OSPF доступны, как только пришёл первый непустой снимок.
   - `details.js`: строка фактов в живом режиме — «Факты собраны HH:MM» без «демо»; если `now − generated_at > ttl` —
     «данные устарели, обновлено N мин назад» (данные всё равно показываются). `rev: 0` / `nodes: {}` — серое «нет данных».
     У ноды с `error` — текст по коду (`unreachable`, `timeout`, `stopped`, `unsupported`, `failed`, неизвестный — общий),
     её факты бледные, `collected_at` — «обновлено N мин назад». Ошибка сбора не рисуется как `oper: down`.
     Кнопка «Обновить сейчас» → `refresh_facts`, затем внеочередной `get_facts` через ~5 с.
   - Живые статусы нод по-прежнему из `session.nodes`, не из `status` в фактах (CONTRACTS §2.7).
   - Новые тексты — языковыми строками (`lang/en` и `lang/ru`), передать в шаблон/JS как остальные `ui_*`.
     Старые русские литералы в `facts.js`/`details.js` не трогать (отдельная задача).
6. **Стили** (`styles.css`): бледная нода/интерфейсы при `error`, отметка «устарело».
7. **mock-менеджер** (`devtools/mock-manager/mock_manager.py`): у `running`-сессии `features: ["consoles", "facts"]`;
   `GET /api/v1/sessions/{id}/state[?since_rev]` отдаёт `fixtures/<lab>.facts.json`, переведённый на `node_id`
   живого проекта (по `name`), с `rev`, `generated_at`, `ttl: 90`; `304` при совпадении `rev`.
   `POST …/state/refresh` → `202` (`409 session_not_running` для неживой); после refresh rev растёт и, для наглядности,
   снимок переключается между `facts.json` и `facts-broken.json`. Описать в `devtools/README.md`.
8. **ui-harness**: заглушка `core/ajax` для `get_facts`/`refresh_facts`, чтобы живой режим проверялся без Moodle.
9. **Тесты PHPUnit** (`tests/`): `fake_manager` с маршрутами state; `manager_client_test` — 200, 304, чужая схема, 409;
   новый `tests/external/facts_test.php` — права (`view` без `use`, наблюдатель чужой группы), `available: false` без `facts`,
   `changed: false` при том же `sincerev`, кэш, refresh для не-`running`.
10. **Версия и документы**: `version.php` → 0.6.0 (новые services и caches), `amd/build` пересобрать
    (`devtools/ui-harness`: `node build.js --amd-build`), SPEC §5.3 — новые функции, `live-state.md` §6–7 — что сделано.

## Проверка

- В сессии: `php -l` по плагину, PHP-синтаксис тестов, `python3 -m py_compile` mock-менеджера, сборка ui-harness,
  ручной прогон живого режима в ui-harness через Chromium (скриншоты L2/L3 с `facts-broken`).
- У владельца (dev-Moodle): `moodle-plugin-ci phpcs`, PHPUnit плагина, `grunt amd` (сверить с собранным),
  запуск лабы через mock-менеджер: слои появляются, «Обновить сейчас» меняет снимок, устаревание по `ttl`.
