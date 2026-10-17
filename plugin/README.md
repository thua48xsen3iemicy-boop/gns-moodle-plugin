# mod_gns3lab

Moodle-плагин для лабораторных работ в GNS3: схема топологии и консоли устройств прямо в курсе.

| Папка | Что там |
|---|---|
| `plugin/` | сам плагин `mod_gns3lab` (пишется по `docs/SPEC.md`; установка — `plugin/README.md`) |
| `devtools/` | `build_zip.sh` — ZIP для установки через интерфейс Moodle, `sync_demo_data.sh` — демо-лаба в плагин, `mock-manager/` — менеджер лаб для разработки (`devtools/README.md`) |
| `prototypes/` | рабочие прототипы UI: вьюер `.gns3` и консоль с имитатором IOS (открываются в браузере как есть) |
| `fixtures/` | пример топологии и демо «живых» данных |
| `docs/` | ТЗ (`SPEC.md`), первый промпт для Claude Code (`FIRST_PROMPT.md`), просьбы и вопросы к менеджеру (`MANAGER_REQUESTS.md`) |

Правила для Claude Code — в `CLAUDE.md`.

## Шлюз консолей

Шлюз (браузер ⇄ WS-консоль Lab Slot) ведёт менеджер: `manager/gateway/` в ветке менеджера `claude/determined-brown-lfgnxy`
(`docs/MANAGER_REQUESTS.md` §12). Плагину от него нужен только контракт: адрес, одноразовый билет, кадры, коды
закрытия 4000/4001. Запуск для разработки — `devtools/README.md`.
