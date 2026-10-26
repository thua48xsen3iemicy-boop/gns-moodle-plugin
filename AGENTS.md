# AGENTS.md — GNS3-лабы в Moodle

Правила для ИИ-агентов (Codex, Claude Code и др.), которые развивают этот репозиторий.
Claude Code читает этот файл через `CLAUDE.md`. Во всех компонентах есть свой `AGENTS.md`:
он подгружается, когда работа идёт в его каталоге, и дополняет этот файл.

Студент открывает элемент курса Moodle, видит схему топологии GNS3 и работает в консолях устройств.
Лаба запускается в LXC-слоте Proxmox, консоли идут через шлюз.

## Компоненты

| Каталог | Что | Стек |
|---|---|---|
| `plugin/` | Moodle-плагин `mod_gns3lab` (сам плагин — `plugin/plugin/`), mock-менеджер, прототипы UI | PHP (Moodle 5.1), ванильный JS (AMD) |
| `manager/` | Lab Manager: сессии, выбор слота, запуск лаб; `manager/gateway/` — шлюз консолей | Python 3.12, FastAPI, PostgreSQL |
| `slot/` | Lab Slot: исполнитель одной лабы внутри LXC | Python, FastAPI, GNS3 2.2.61 |
| `agent/` | Lab Node Agent: привилегированный помощник на узле Proxmox, mTLS | Python, FastAPI |
| `install/` | `lab-provision`, сборка и проверка неизменяемых выпусков на CephFS | Python, shell |
| `docs/` | общие планы и отчёты: пилот, выпуски | — |
| `tools/audit/` | проверка гипотез пилота | Python |

Компоненты общаются только по контрактам. Источник правды по форматам —
`manager/docs/CONTRACTS.md`, по плагину — `plugin/docs/SPEC.md`.

## Экономия контекста — обязательно

1. **Работай в каталоге компонента задачи.** Код другого компонента не читай: чтобы понять
   соседа, достаточно контракта. Исключение — задача прямо на стыке компонентов.
2. **Не читай и не ищи.** Эти пути исключены из `rg` файлом `.ignore`, а для Claude Code
   сгенерированное и сторонние библиотеки закрыты на чтение в `.claude/settings.json`:
   - `plugin/plugin/amd/build/` и `*.map` — генерируются `grunt amd`;
   - `plugin/plugin/amd/src/xterm.js`, `xterm-addon-fit.js`, `novnc.js` — сторонние библиотеки;
   - `plugin/prototypes/*.html` — эталон UI по 60–140 КБ. Открывай, только если задача про них,
     и только нужный фрагмент. Искать внутри них — `rg --no-ignore <шаблон> plugin/prototypes`.
3. **Большие документы не читай целиком.** Сначала оглавление: `grep -n '^#' <файл>`,
   потом нужный раздел — `sed -n '<от>,<до>p' <файл>`. Целиком — только `plugin/docs/SPEC.md`.
   Больше 20 КБ: `plugin/docs/MANAGER_REQUESTS.md`, `manager/docs/{DESIGN,CONTRACTS,PLUGIN_REQUESTS}.md`,
   `manager/README.md`, `slot/README.md`, `slot/docs/ALIGNMENT.md`, `docs/pilot-readiness/README.md`,
   `docs/release-preparation/PLAN.md`, `install/RELEASES.md`, `install/README.md`, `agent/README.md`.
4. **Короткий вывод команд:** `pytest -q`, `pip -q`, `apt-get -qq`. Длинный лог — в файл,
   потом `tail -n 40` или `grep -n -i error`.
5. **Тесты — только затронутого компонента.** Все наборы — когда меняется контракт или общий код.
6. **Стенд в контейнере сессии не поднимай.** Proxmox, LXC, CephFS, GNS3 и настоящий Moodle там
   не запустятся. В сессии — юнит-тесты, линтеры, `php -l`, mock-менеджер. Проверка на стенде —
   по `install/README.md`; её запускает владелец, а агент пишет, что именно проверить.

## Общие правила

- С владельцем проекта общайся **по-русски**. Код, комментарии в коде и сообщения коммитов — **на английском**.
- Контракт не выдумывай. Новое поле, код ошибки или маршрут вноси вместе с правкой
  `manager/docs/CONTRACTS.md`. Просьбы и ответы между компонентами лежат в `*_REQUESTS.md`
  в `docs/` компонентов. Если в документах противоречие — спроси.
- Изменение компонента — отдельный коммит. Если нужно, подними его версию: `plugin/plugin/version.php`,
  `manager/lab_manager/__init__.py`, `slot/release.json`, `version=` в `agent/agent.py` и заголовок `README.md`.
- Добавил, удалил или переименовал файл в `manager/`, `slot/` или `agent/` (любой, включая документацию и тесты) —
  обнови явный перечень `SOURCE_FILES` в `install/lab-release.py`: сборщик выпусков отклоняет дерево с файлом не из
  перечня. Поднял версию компонента — обнови `compatibility.components` в `install/lab-release-build.json.example`.
  Расхождение ловят тесты `install/tests` (`test_allowlist_matches_delivered_trees`); запускай их в таких изменениях.
- Не коммить секреты, `.env`, ключи, сертификаты, дампы БД.
- Зависимости Python проверены на 3.12; версии закреплены в `constraints-tested.txt`.
  Новую зависимость добавляй только при необходимости и тоже закрепляй там.

## Тесты Python

Один venv на все компоненты (из корня репозитория):

```sh
python3 -m venv /tmp/gns-venv
/tmp/gns-venv/bin/pip install -q -c constraints-tested.txt -r manager/requirements.txt \
  -r manager/gateway/requirements.txt -r slot/requirements.txt -r agent/requirements-test.txt pytest
```

Затем только нужный набор (`PY=/tmp/gns-venv/bin/python`):

```sh
PYTHONPATH=manager $PY -B -m pytest -q -p no:cacheprovider manager/tests   # без DATABASE_URL тесты PostgreSQL пропускаются
PYTHONPATH=slot    $PY -B -m pytest -q -p no:cacheprovider slot/tests
PYTHONPATH=agent   $PY -B -m pytest -q -p no:cacheprovider agent/tests
                   $PY -B -m pytest -q -p no:cacheprovider install/tests
```

Под root часть тестов `agent/` и один тест `slot/` падают: они ждут обычного пользователя
(UID 0 отклоняется, права на чтение не действуют). Это окружение, а не регрессия: сравни с `main`
до правок и не подгоняй тесты под root.

Плагин: `git ls-files 'plugin/*.php' | xargs -n1 php -l | grep -v '^No syntax errors'`.
Проверки Moodle (phpcs, PHPUnit, Behat, `grunt amd`) — только в dev-Moodle, см. `plugin/AGENTS.md`.
