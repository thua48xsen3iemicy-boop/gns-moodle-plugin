# mod_gns3lab

Moodle-плагин для лабораторных работ в GNS3: схема топологии и консоли устройств прямо в курсе.

| Папка | Что там |
|---|---|
| `plugin/` | сам плагин (пишется по `docs/SPEC.md`) |
| `gateway/` | шлюз консолей: браузер (xterm.js) ⇄ GNS3 `/console/ws` в LXC-слоте |
| `prototypes/` | рабочие прототипы UI: вьюер `.gns3` и консоль с имитатором IOS (открываются в браузере как есть) |
| `fixtures/` | пример топологии и демо «живых» данных |
| `docs/` | ТЗ (`SPEC.md`) и первый промпт для Claude Code (`FIRST_PROMPT.md`) |

Правила для Claude Code — в `CLAUDE.md`.

## Шлюз: быстрая проверка

```bash
pip install aiohttp
python3 gateway/tests/test_gateway.py
```
