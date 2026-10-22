# AGENTS.md — Lab Node Agent

Пути — относительно `agent/`. Общие правила, экономия контекста и команды тестов — в `../AGENTS.md`.

## Где что

- `agent.py` — весь сервис (FastAPI, mTLS, вызовы `pct`), ~480 строк.
- `guest/lab-slot-init` — инициализация внутри гостя; `install-node.sh`, `lab-node-agent.service` — установка на узел.
- `manager-example.py` — пример клиента; `make-demo-pki.sh` — тестовая PKI, не для продакшена.

## Правила

- **Агент привилегированный, граница доверия жёсткая** (`README.md`, «Repository integration and trust boundary»).
  Не добавляй приём shell-команд, абсолютных путей, путей монтирования, VLAN/MAC/IP или sudo от клиента.
  Работа только с LXC в настроенном диапазоне VMID с тегом `labslot` и `unprivileged: 1`.
- mTLS обязателен. Не добавляй `verify=False`, `-k`, plain HTTP или доверие proxy-заголовкам.
- Проверки путей (владелец, права, ACL, `O_NOFOLLOW`) не упрощай.
- Версия — `version=` в `agent.py`. Контракт с менеджером — `../manager/docs/CONTRACTS.md` §5 и `../manager/docs/AGENT_REQUESTS.md`.
- Под root часть тестов падает (UID 0 отклоняется) — см. `../AGENTS.md`.
