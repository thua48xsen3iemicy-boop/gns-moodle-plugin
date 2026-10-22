# AGENTS.md — Lab Manager

Пути — относительно `manager/`. Общие правила, экономия контекста и команды тестов — в `../AGENTS.md`.

## Где что

- `run.py`, `lab_manager/app.py` — FastAPI, API для плагина (`/api/v1/...`).
- `lab_manager/orchestrator.py` — жизненный цикл сессий и слотов, самый большой модуль (~900 строк).
  Не читай целиком: `grep -n 'def ' lab_manager/orchestrator.py`, потом нужная функция.
- `lab_manager/slot_client.py`, `agent_client.py`, `proxmox.py` — клиенты слота, агента узла и Proxmox API.
- `lab_manager/store.py`, `db.py`, `migrations/NNN_*.sql` — PostgreSQL.
- `gateway/console_gateway.py` — шлюз консолей, отдельный процесс в том же LXC.
- `tests/fakes.py` — поддельные слот, агент и Proxmox для тестов.

## Правила

- **Миграции:** только новый файл со следующим номером. Применённые миграции не меняй. Сервис сам
  не мигрирует: администратор запускает `python -m lab_manager.admin migrate`.
- Контракт с плагином, слотом и агентом — `docs/CONTRACTS.md`. Меняешь формат — правь его в том же коммите.
  Ответы плагину — `docs/PLUGIN_REQUESTS.md`, просьбы к агенту и слоту — `docs/AGENT_REQUESTS.md`, `docs/SLOT_REQUESTS.md`.
- Контракт браузер ↔ шлюз (билеты, кадры, коды закрытия 4000/4001) меняется только вместе с плагином.
- Токены слотов, ключи mTLS и секрет плагина не попадают в ответы API, логи и браузер.
- Версия — `VERSION` в `lab_manager/__init__.py` и заголовок `README.md`.
- `docs/DESIGN.md` (42 КБ) читай по разделам: `grep -n '^#' docs/DESIGN.md`.
  Тесты PostgreSQL пропускаются без `DATABASE_URL`, это нормально.
