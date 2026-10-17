# mod_gns3lab

Moodle-плагин для лабораторных работ в GNS3: схема топологии и консоли устройств прямо в курсе.

| Папка | Что там |
|---|---|
| `plugin/` | сам плагин `mod_gns3lab` (пишется по `docs/SPEC.md`; установка — `plugin/README.md`) |
| `devtools/` | `build_zip.sh` — ZIP для установки через интерфейс Moodle, `sync_demo_data.sh` — демо-лаба в плагин |
| `gateway/` | шлюз консолей: браузер (xterm.js) ⇄ WS-консоль Lab Slot `/v1/console` в LXC-слоте |
| `prototypes/` | рабочие прототипы UI: вьюер `.gns3` и консоль с имитатором IOS (открываются в браузере как есть) |
| `fixtures/` | пример топологии и демо «живых» данных |
| `docs/` | ТЗ (`SPEC.md`) и первый промпт для Claude Code (`FIRST_PROMPT.md`) |

Правила для Claude Code — в `CLAUDE.md`.

## Шлюз: быстрая проверка

```bash
pip install aiohttp
python3 gateway/tests/test_gateway.py
```
