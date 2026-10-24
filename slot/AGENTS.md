# AGENTS.md — Lab Slot

Пути — относительно `slot/`. Общие правила, экономия контекста и команды тестов — в `../AGENTS.md`.

## Где что

- `main.py` — FastAPI-приложение и `POST /v1/fence`; `api/` — маршруты и логика:
  `labs.py`, `lifecycle.py`, `nodes.py`, `console.py`, `health.py`, `auth.py`, `models.py`, `qga.py`.
- `release.py`, `release.json` — проверка выпуска и версия; `tools/check_slot.py` — проверка слота на стенде.

## Правила

- **Fencing — инвариант.** Любой изменяющий запрос принимается только после `POST /v1/fence`
  и с заголовками `X-Lab-Epoch`/`X-Lab-Generation`. После рестарта нужен строго более новый владелец.
  Не ослабляй эти проверки.
- **QGA — только через брокер** (`api/qga.py`, `Lifecycle.qga`). Не открывай `/opt/gns3/qga/*.sock`
  напрямую: сокет одноклиентский, обмены с нодой должны идти по очереди. Новый потребитель выбирает
  приоритет (`LIFECYCLE` > `COMMAND` > `METRICS`); фоновые вызовы — с `wait=False` или `max_age`.
- Версия GNS3 закреплена: **2.2.61**. Общие образы (`/opt/gns3/images`) никогда не изменяются.
- Удаление нод без подтверждения запрещено намеренно (`README.md`, «Node Wipe»).
- Контракт с менеджером — `../manager/docs/CONTRACTS.md` §4, сверка — `docs/ALIGNMENT.md`.
- Версия — `release.json` и заголовок `README.md`.
- `README.md` (37 КБ): актуальное — «Жизненный цикл и гарантии» и «API». Разделы «Версия 0.x» — история,
  открывай только по необходимости.
- После правок: тесты `slot/tests` и `python3 -m compileall -q slot`.
