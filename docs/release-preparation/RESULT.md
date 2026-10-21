# RESULT — подготовка неизменяемых выпусков, первый этап

Статус: реализация готова к независимому ревью; **стендовая приёмка не выполнена**.
Дата: 2026-10-21. Ветка: `codex/immutable-cephfs-releases`.

## Основание и коммиты

- Исходный актуальный main: `362f73207721c39103f7d90355caa6a5fd2132e9`.
- PLAN v2: `774602ac32793a6c4cadf660e755874851f142e6`.
- APPROVED руководителя в TASK.md: `715fc850b881a76b0072268550f70169ef6d1036`.
- Реализация и тесты: `57b72f5f7588a0366c5cda1069c3ef6ee4745cfa`.
- Закрепление абсолютных системных executable и тест защиты от подмены PATH:
  `8205ff807592e1db19bd40b1c57db66dee2d7495`.
- Этот отчёт добавлен отдельным последующим коммитом; он не меняет рабочий код.

## Фактические изменения

`install/lab-provision` получил ранний dispatch `release build|verify|list`
до production load_conf. Старые команды/установка остаются прежними.

Добавлены `install/lab-release.py`, `install/release-builder.py`, пример
`lab-release-build.json.example`, операторская инструкция `install/RELEASES.md`
и `install/tests/test_release.py`. В install README и pilot-hardening — ссылки
на новый первый этап, без замены существующего регламента обновления.

Реализованы:

- Экспорт только blobs полного SHA Git commit по явному версионируемому списку
  Manager/Slot/Agent/constraints, без checkout/pull общего дерева, untracked,
  production конфигурации, секретов и рабочих данных. Дополнительные tracked
  файлы в поставляемых деревьях отвергаются; обнаруживается PEM private key
  внутри разрешённого исходника. Это не универсальный secret scanner.
- Manifest `gns3lab.release/1`: SHA/tree продукта, отдельные version/SHA и хеши
  файлов сборщика, provenance чистых шаблонов, runtime/ABI/architecture, версии
  компонентов, SQL/schema, совместимость, locks/wheels и полный inventory.
  Проверяются типы, права, root owner, отсутствие ACL, extra/missing/changed
  файлы, допустимые venv links, зависимости и вложенный Slot manifest.
- `slot/release.py` и `slot/release.json` остаются исходными, без изменений.
  Их files — сверяемое подмножество полного inventory. При build проверка Slot
  выполняется в builder; verify/list применяют только доверенную политику
  инструмента, не импортируют и не запускают код/venv/hooks/.pth пакета.
  Абсолютные системные пути защищают dispatcher/host-команды от выбора
  executable из пакета через подменённый PATH; host Python — 3.11+.
- Резервация ID через mkdir на CephFS, уникальный скрытый staging, публикация
  только через renameat2 RENAME_NOREPLACE, без перезаписывающего fallback;
  fsync файлов и директорий. Существующий final, в том числе пустой, не меняется.
  Неопределённый исход после rename не вызывает удаления/отката final.
- Полные новые клоны двух чистых LXC templates перед каждой попыткой на
  существующем или отдельном узле PVE. Проверки ролей/VMID/локальности,
  CPU/RAM/swap/disk/I/O, template config/runtime fingerprints, новый rootfs;
  отдельные locks builders. Только source RO и новые output/venv RW.
- Wheels разрешаются в соответствующем Ubuntu или Debian runtime с constraints
  выбранного commit. Только binary wheels; полный pinned lock с SHA256;
  установка через no-index/require-hashes, pip check, origin/prefix/shebang и
  import checks в builder. Manager включает gateway. Agent поставляет wheels/
  lock, его тестовый venv не попадает в опубликованный пакет.
- Непривилегированные smoke checks через RO mounts. Перед публикацией —
  остановка, снятие mounts, удаление созданных builder CT и проверка отсутствия
  оставшихся volumes VMID. Ошибка очистки блокирует публикацию. При uncertain
  pct outcome автоматическое разрушение ресурсов запрещено; metadata, lock и
  staging остаются для ручного recovery. Новый запуск не использует старый rootfs.

Новые команды не вызывают старые install steps, migrate, systemctl/restart,
slot-enable или API выдачи. Код Manager/Agent/Slot, units, requirements,
constraints, SQL/SQLite, ownership/fencing, reservation/cleanup не изменены.
Никакой выпуск на рабочем CephFS не строился и не применялся.

## Команды и результаты локальных проверок

Рабочий Python: `/workspace/gns-dev-venv/bin/python`, версия 3.12.14.

| Команда | Фактический результат |
|---|---|
| `/workspace/gns-dev-venv/bin/python -B -m pytest -q -p no:cacheprovider --basetemp work/release-tests install/tests/test_release.py` | **54 passed**, 10.47 s |
| `PYTHONPATH=slot /workspace/gns-dev-venv/bin/python -B -m pytest -q slot/tests` | **45 passed**, 1 существующее предупреждение Starlette/httpx |
| `PYTHONPATH=manager /workspace/gns-dev-venv/bin/python -B -m pytest -q manager/tests` | **146 passed, 90 skipped**, 3 существующих aiohttp AppKey warnings |
| `PYTHONPATH=agent /workspace/gns-dev-venv/bin/python -B -m pytest -q agent/tests` | **66 passed**, 1 существующее предупреждение Starlette/httpx |
| `bash -n install/lab-provision` | OK |
| AST parse всех новых Python-файлов и JSON parse примера конфига | OK |
| `bash install/lab-provision release --help`, help всех трёх подкоманд | OK; dispatch не требует root/pct/production lab.conf |
| `git diff --check` и `git diff --cached --check` | OK |
| `builder_identity()` из чистого checkout после коммитов реализации | version 1.0.0, SHA `8205ff807592e1db19bd40b1c57db66dee2d7495`, оба хеша инструментов совпали с Git blobs |

Итого: **311 passed, 90 skipped** по четырём наборам. 90 пропусков Manager —
PostgreSQL-тесты: переменная DATABASE_URL для отдельной throwaway БД не настроена.
Их не выдаём за пройденные. Первоначальный запуск Agent из корня без PYTHONPATH
дал collection error из-за импорта namespace `agent`; правильная команда выше
успешно проверяет существующие тесты. Это уточнение команды, не изменение Agent.

Новые тесты охватывают изменение/удаление/добавление файлов, wheel/venv артефакты,
права, forbidden links/hardlinks/FIFO, unsafe paths, schema/policy и Slot manifest,
экспорт грязного checkout/запрет ключей, независимые SHA продукта/сборщика,
отказ dirty builder, no-execution traps и отсутствие вызовов executable policy.

На локальной Linux FS реально запускались два процесса с конкурирующим
NOREPLACE publish и SIGKILL до/после rename; существующий пустой final не
перезаписывается, смешанного дерева нет, прерванный staging не показывается
готовым. Инъекция ошибки fsync после rename оставляет полный final для проверки.
Отдельные модельные тесты покрывают PVE lifecycle, свежие полные clones,
запрет чужого/занятого/uncertain VMID, последовательные попытки, полный host
pipeline и блокировку публикации при ошибке удаления builders.

Исполнитель непривилегированный: root UID/GID и DAC privilege в части fixtures
моделируются; CLI не имеет флага обхода root/CephFS проверок. Wheels в fixtures
синтетические и не устанавливались. PVE/LXC callbacks в этих тестах — модель,
не реальное клонирование, установка зависимостей или подтверждение runtime ABI.

## Проверки, НЕ выполненные на настоящем стенде

1. CephFS из двух клиентов: mkdir reservation, RENAME_NOREPLACE на конкретных
   kernel/client/Ceph версиях, сетевые отказы, ENOSPC, crash durability/fsync и
   согласованная видимость final. Локальный Linux результат этого не доказывает.
2. Настоящие полные clones и удаление CT/rootfs/volumes, pmxcfs/pct/pvesm вывод,
   восстановление после аварии, отсутствие остатков прежней попытки.
3. Реальные CPU/RAM/disk/I/O limits. Используемый storage backend должен давать
   работающий cgroup2 IO cap на устройство rootfs клона. При изменении device ID
   или неподдерживаемом backend программа откажет; фиктивные лимиты недопустимы.
4. Download/resolve/install полного wheel closure с SHA256 в Ubuntu 24.04/Python
   3.12 и отдельном Debian/PVE runtime, offline install, pip check, system libs,
   module origins, fixed guest paths, shebang и RO smoke checks.
5. Настоящие unprivileged UID mapping, CephFS ACL/ownership и невозможность
   записи из учебных CT в code/venv; аппаратная/сетевая изоляция builders.
6. Импорты/Slot check из точных guest путей после физической публикации,
   неизменность рабочих сервисов/CT при сборке по аудиту оператора.
7. Отдельная throwaway PostgreSQL для 90 пропущенных regression tests Manager.

Среда этого исполнения не содержит выделенных PVE/CephFS/LXC и target profiles.
Полноценный release build здесь не выполнялся. Одобрение плана и локальные
тесты не означают приёмку на инфраструктуре или допуск пилота.

## Ограничения и соответствие плану

Область файлов и архитектурные решения PLAN v2 сохранены. Уточнены обязательные
поля конфига: config fingerprint шаблона проверяется автоматически вместе с
runtime/interpreter/system packages; SHA исходного чистого image задаёт
сопровождающий и он явно помечен `maintainer_attested`. Связь image → immutable
template доверяется оператору, полный rootfs hash программой не проверяется.
Обязательные template/OS/network/resource входы не заменяются догадками.

Нумерованная схема PostgreSQL выводится из SQL, сейчас требуется ровно 006.
State compatibility/rollback — заявления сопровождающего, hashes не являются
подписью/доказательством подлинности. Существующая runtime Slot проверка сохраняет
свою прежнюю область; полный аудит зависимостей выполняет release verify.

Применение структуры выпуска к нынешнему SLOT_CODE_REVISION/LAB_CODE_DIR,
установка Agent из wheelhouse, миграции и переключение Manager/Slot остаются
следующим отдельным этапом. APPROVED/TASK.md не переписывались исполнителем;
REVIEW.md должен подготовить независимый проверяющий на конкретный PR commit.
Самостоятельные merge, развёртывание и приёмка рабочего стенда не выполнялись.
