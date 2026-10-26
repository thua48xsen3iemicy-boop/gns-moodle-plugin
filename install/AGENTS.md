# AGENTS.md — развёртывание и выпуски

Пути — относительно `install/`. Общие правила, экономия контекста и команды тестов — в `../AGENTS.md`.

## Где что

- `lab-provision` — bash (~1200 строк): шаги развёртывания стенда от root на узлах Proxmox (`README.md`).
- `lab-release.py`, `release-builder.py` — сборка и проверка неизменяемых выпусков на CephFS (`RELEASES.md`).
- `tests/test_release.py` — тесты выпусков; `TROUBLESHOOTING.md` — разбор сбоев на стенде.

## Правила

- **Не запускай `lab-provision` и шаги выпуска в сессии.** Они рассчитаны на узлы Proxmox (`pct`, CephFS).
  В сессии: `bash -n lab-provision`, `python3 -m compileall -q lab-release.py release-builder.py`, тесты `install/tests`.
- Выпуски неизменяемые: опубликованный выпуск не переписывается, только новый.
- Шаги `lab-provision` идемпотентны, уже созданные секреты не пересоздаются. Сохраняй это.
- Секреты не выводи в stdout и логи.
- `SOURCE_FILES` в `lab-release.py` — явный, проверенный человеком перечень поставляемых файлов (`RELEASES.md`), а не
  шаблон: не заменяй его автоматическим включением. Тест `test_allowlist_matches_delivered_trees` сверяет его с
  `git ls-files` и показывает, каких файлов не хватает и какие лишние.
