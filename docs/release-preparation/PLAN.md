# Подготовка неизменяемого выпуска на CephFS — PLAN v1

Статус: **предложение; требуется APPROVED до изменения рабочего кода**.
Дата: 2026-10-21.
Ветка: `codex/immutable-cephfs-releases`.
База: актуальный `origin/main`, полученный `git fetch origin main`,
`362f73207721c39103f7d90355caa6a5fd2132e9`.
Каталог координации: `docs/release-preparation/` в этом репозитории;
пользователь поручил выбрать расположение исполнителю.

## 1. Область и порядок работы

Последовательность: TASK.md → PLAN.md → APPROVED → реализация → RESULT.md →
независимый REVIEW.md. APPROVED фиксирует конкретный commit плана и согласованные
решения из раздела 10. Исполнитель не создаёт одобрение и независимый отзыв за
руководителя/ревьюера. Сейчас подготовлены только документы.

Первый этап создаёт и проверяет пакет, не применяет его к стенду. Не меняются
владение, fencing, бюджеты, очистка, SQL/SQLite, сервисы и монтирования рабочих CT.
Существующие команды установки сохраняют поведение. Вызов старых `agent`,
`manager`, `slot`, `venv` из нового сборщика запрещён: эти шаги меняют стенд.

## 2. Что обнаружено в коде и документации

1. `install/lab-provision:cmd_venv` использует первый учебный слот или рабочий CT
   менеджера, `venv --clear`, временный RW mount и смену владельца общего venv.
   Это соответствует старому установщику, но несовместимо с безопасной подготовкой
   нового выпуска. Новый путь сборки должен быть отдельным.
2. `slot/run.py`, `manager/run.py` и systemd units требуют фиксированных путей
   `/srv/gns3-ro/{slot,manager}` и venv непосредственно внутри них. Agent ставится
   локально в `/opt/lab-node-agent`, запускается Python узла, не Python Ubuntu CT.
3. `SLOT_CODE_REVISION` и `LAB_CODE_DIR` выбирают только источник кода Slot.
   Agent и Manager ожидают `<code-root>/<revision>`; это не селектор полного
   выпуска с Manager и Agent. Не переопределять этот контракт в первом этапе.
4. `slot/release.py` проверяет выбранные расширения файлов, исключает venv и
   кэши. Поэтому нынешняя проверка не покрывает зависимости и добавленные файлы
   произвольных расширений. `build_id` может быть хешем содержимого, не Git SHA.
   На исходном SHA `verify()` проходит; подменять его идентичность не требуется.
5. `constraints-tested.txt` фиксирует версии и транзитивные зависимости Python
   3.12, но не хеши пакетов и не совместимость с произвольной версией Python PVE.
   Старые ручные команды pip в README не всегда используют constraints, хотя
   текущий установщик их уже использует. Новая инструкция будет явно применять
   lock с хешами, не объявляя constraints готовым универсальным lock.
6. `manager/lab_manager/preflight.py` требует **точного** равенства схемы БД
   `latest_version()`, сейчас 006. Это не обещание совместимости со схемами 1–6.
   Slot хранит SQLite на rootfs CT, Agent — fencing на rootfs узла; эти данные
   не принадлежат выпуску. У Slot нет отдельной нумерованной схемы SQLite;
   обещание совместимости/отката нельзя вывести только из номера компонента.
7. Некоторые старые части README Manager ссылаются на feature-ветку Agent и
   HTTP, тогда как pilot-hardening задаёт актуальные версии и TLS. Они не будут
   использованы как основание для изменения протоколов или развёртывания.

## 3. Интерфейс

Сохраняется предложенная пользователем группа команд:

```sh
P=/path/to/checkout/install/lab-provision
$P release build r20261021_01 --ref <full-40-hex-commit> --config /etc/lab-release-build.json
$P release verify r20261021_01 --root /mnt/pve/cfs/gns3-releases
$P release list --root /mnt/pve/cfs/gns3-releases
```

`release` обрабатывается **до** старого `load_conf`. Иначе даже просмотр пакетов
требовал бы `pct`, production lab.conf, секреты и сведения о рабочих CT.
Сборка требует root на выделенном сборочном узле PVE и явного JSON-конфига;
verify/list не выполняют `pct`, не загружают shell-конфиги, работают без сети,
не требуют root при наличии доступа на чтение. Root по умолчанию —
`/mnt/pve/cfs/gns3-releases`; запретить попадание в общий checkout, secrets,
students и существующие рабочие каталоги. Проверять mountpoint/FSTYPE CephFS,
каноничность, отсутствие symlink и небезопасных ACL/прав у предков.

`--ref` принимает только полный SHA Git commit, уже доступного в локальном repo;
никаких git pull/checkout рабочего дерева. Отсутствующий объект — ошибка с
инструкцией fetch в отдельный сборочный checkout. Release ID соответствует
`[A-Za-z0-9][A-Za-z0-9_-]{0,63}`, без `.` и разделителей пути.

## 4. Формат пакета и manifest

```text
/mnt/pve/cfs/gns3-releases/
  .staging/<release-id>.<random>/   # только root, никогда не готовый выпуск
  .locks/<release-id>/             # атомарная резервация идентификатора
  <release-id>/
    manifest.json                 # gns3lab.release/1
    constraints-tested.txt
    manager/                      # код, gateway, SQL, units, venv/
    slot/                         # код, прежний release.json, unit, venv/
    agent/                        # код, guest helper, installer и unit; без venv
    dependencies/
      manager/{requirements.lock,wheels/}
      slot/{requirements.lock,wheels/}
      agent/{requirements.lock,wheels/}
    verification/                 # обезличенные результаты сборочных проверок
```

Manager и Slot содержат venv, собранные по будущим **guest** путям. Для Agent
поставляется wheelhouse и полный lock под отдельно заявленный профиль PVE;
его проверочный venv создаётся в builder по `/opt/lab-node-agent/venv`, но не
выдаётся за переносимый venv для установки на любой узел. Установка Agent из
нового пакета относится к следующему этапу; прежний installer не вызывается.

Manifest содержит:

- schema, release_id, source.git_sha, source.tree_sha, время UTC и версию формата;
- версии Manager/Slot/Agent, gateway как часть Manager, исходную Slot identity;
- профили ОС, Python major/minor и ABI, architecture, interpreter path,
  fingerprint сборочного шаблона/системных пакетов; точные runtime пути;
- проверенные версии и SHA256 wheel/lock, установленные distributions,
  результаты `pip check`, импортов и проверки фиксированных путей;
- PostgreSQL: список и хеши миграций, требуемая схема, сейчас min=max=6;
  отдельно заявления сопровождающего о Slot state/Agent state, совместимости
  компонентов и откате (`not_assessed` допустимо только как явное отсутствие
  гарантии отката, а не как подтверждение совместимости);
- полный inventory относительных путей: regular file/hash/size/mode, directory/
  mode, разрешённый symlink/target. Никаких абсолютных имён inventory, `..`,
  устройств, sockets, FIFO или произвольных ссылок.

Сам manifest исключается из собственного inventory; строгий JSON без дубликатов
ключей, закрытая версия схемы и сверка точного множества путей обязательны.
Для внешнего закрепления выводится SHA256 manifest: одновременная подмена кода и
manifest доверенным администратором не обнаруживается без внешнего эталона.
Хеши подтверждают целостность относительно manifest, **не подлинность** выпуска.
Подписи и PKI поставки в этот этап не добавляются.

Автоматически извлекаются Git SHA, версии из исходного кода без его исполнения,
миграции, фактический runtime профиль, inventory, lock и результаты проверок.
Сопровождающий в JSON-конфиге задаёт ожидаемые профили/идентификаторы шаблонов,
совместимость state и компонентов. Неверное/неполное заявление не заменяется
догадкой сборщика; факты должны совпасть с заявленными ограничениями.

## 5. Получение кода и согласование с Slot

Экспортировать только blobs выбранного commit через Git object database,
используя явный список поставляемых путей Manager/Slot/Agent и constraints.
Рабочее дерево, untracked файлы и `.git` не копируются. Отвергать submodule,
неожиданные symlink и неизвестные пути; список поставки версионируется в сборщике.
Никогда не копировать каталоги целиком с CephFS/узла/CT.

В список входят нужные Slot docs/examples/tests, которые уже перечислены в
его `release.json`, и безопасные `.example`-шаблоны; фактические `.env`, lab.conf,
sites.json/slots.json стенда, private keys, tokens, SQLite, logs, caches,
проекты/образы студентов и данные builder не входят. Тесты и документация не
нужны для запуска, но сохраняют проверяемость существующего Slot manifest.
Запрещённые имена/расширения проверяются дополнительно. Это защита от случайного
захвата состояния, не универсальный детектор секрета в произвольном исходнике.

Сохранить `slot/release.py` и `slot/release.json` **без изменения** из выбранного
commit. До публикации проверить существующий manifest и доказать, что его
`files` равен проекции общего inventory на `slot/release.py:files()`;
версия и build_id должны совпадать с записанной в общем manifest Slot identity.
Несоответствие — отказ, без автоматического `seal` повреждённого среза.
Общий manifest покрывает весь пакет, включая venv, wheels и вложенный manifest;
прежний Slot manifest остаётся проверяемым подмножеством, не вторым независимо
генерируемым описанием тех же файлов. Старый Slot readiness не начинает
хешировать site-packages на каждый запрос. Для полного аудита — release verify.

## 6. Сборочная среда и зависимости

Использовать выделенный сборочный узел и два заранее подготовленных одноразовых
unprivileged LXC, не входящих в учебный кластер выдачи: Ubuntu 24.04/Python 3.12
для Manager/Slot и Debian/Python того же профиля, что целевые узлы PVE, для Agent.
Не клонировать CT с ключами или состоянием стенда. Не использовать рабочие CT.
PVE/kernel, QEMU/GNS3, nginx/PostgreSQL и системные библиотеки — внешние требования,
не содержимое Python venv; версии и границы проверки фиксируются явно.

Пример полей `/etc/lab-release-build.json` (формат будет документирован):
`release_root`, `source_repo`, `ubuntu_builder_vmid`, `agent_builder_vmid`,
ожидаемые OS/Python/ABI/arch и fingerprints, совместимость state/компонентов.
Файл без секретов. Builder требует специальный tag `labreleasebuilder`, stopped
состояние, отсутствие production tags, passthrough, пользовательского idmap и
посторонних bind mounts. Эксклюзивная блокировка builder на узле обязательна.
Выделенность подтверждает оператор; программа не может доказать назначение CT
одним совпадением tag. Все изменения CT ограничены проверенными builder VMID.

В staging код монтируется в Ubuntu builder на фиксированные guest пути; запись
разрешается только в новые venv и служебный временный каталог зависимостей.
Builder не получает старые releases, checkout, secrets или students.
Слотам не предоставляется RW-доступ ни к staging, ни к опубликованным файлам.

Для каждого профиля отдельно:

1. Разрешить requirements с constraints выбранного commit, включая gateway
   для Manager; скачать полное транзитивное замыкание wheels. Только binary
   wheels, без sdist/произвольных install hooks; отсутствие wheel — отказ.
   Agent разрешается в его Debian/Python builder, а не кросс-сборкой из Ubuntu.
2. Записать полный pinned lock с SHA256 каждого wheel; затем устанавливать
   только из сохранённого wheelhouse: `pip install --no-index --require-hashes
   --find-links <wheelhouse> -r <requirements.lock>`.
3. Создать Manager/Slot venv по `/srv/gns3-ro/{component}/venv` и тестовый Agent
   по `/opt/lab-node-agent/venv`; `pip check`, соответствие lock/installed
   distributions, импорты, точные `sys.prefix`, module origins и shebang.
   Записать отдельно bootstrap packages venv; они тоже покрываются inventory.
4. Выполнить Slot `release.py check` под gns3 и безопасную проверку импортов
   Manager/gateway/Agent без lifespan/сервисов, pct, БД, GNS3 и живых секретов.
   Manager preflight, требующий рабочие сертификаты и БД, не вызывать и не
   выдавать импорт за проверку готовности всего стенда.
5. Остановить builder, удалить временные RW mounts, отозвать права mapped root,
   выставить host root:root, запретить запись group/other и extended ACL.
   Повторно смонтировать готовые Manager/Slot RO в builder, проверить чтение и
   импорты ожидаемыми непривилегированными пользователями, запрет записи;
   снять mounts/остановить builder перед финальным хешированием.

Одинаковый guest путь допускает перенос физического host каталога staging →
release; он не разрешает перенос venv между guest путями/ОС/Python/архитектурами.
Допустимы только заранее проверенные venv interpreter symlinks на системный
Python целевого профиля и внутренние ссылки без выхода за компонент; все их
targets входят в manifest и проверяются. Остальные symlinks запрещены.

## 7. Публикация и восстановление

До сборки атомарно создать `.locks/<id>` через mkdir, записать owner/UUID/узел и
SHA; существующий lock или final path — отказ. Lock не на PID, не снимается
автоматически по возрасту и не заменяется межузловым локальным flock.
Staging уникален, mode 0700, на том же CephFS. Публиковать только после всех
проверок, остановки builder, снятия RW mounts, назначения прав, fsync файлов и
директорий. Inventory составляется после окончательных изменений пакета.

Последний шаг — **renameat2(RENAME_NOREPLACE)** staging → `<root>/<id>`;
при EEXIST отказ, при неподдерживаемой операции отказ без fallback на
перезаписывающий rename/mv. Нужен стендовый тест этой операции на конкретных
клиенте/kernel/CephFS; локальный POSIX тест не доказывает поддержку CephFS.
После успеха fsync родителя. Неопределённый итог fsync/обрыва после rename
сообщается явно: проверить существующий final через verify, не удалять и
не пересобирать его. При гонке publish не может заменить существующий каталог,
включая пустой. Корректность не зависит только от предварительного exists().

Сигнал/ошибка до rename оставляет только скрытый staging/lock; final не возникает.
Trap пытается остановить/размонтировать только свои builder ресурсы; при ошибке
очистки сохраняет исходную ошибку и recovery metadata. SIGKILL/сбой узла
обрабатывается оператором. Никакого автоматического resume того же staging.
Перед удалением старого lock/staging оператор подтверждает, что нет сборщика,
CT остановлены и RW mounts сняты; путь сверяется с recovery metadata.
Если final уже появился — только verify, без cleanup опубликованного каталога.

Опубликованный пакет root-owned, файлы 0444/0555, каталоги 0555, без writable ACL;
администратор CephFS/root остаётся доверенным и технически может изменить права.
Новый выпуск строится в другом staging и никогда не меняет предыдущий.
`list` исключает dot-каталоги, не следует symlink; целостность каждого видимого
выпуска проверяется, повреждение показывается как INVALID с ненулевым статусом,
без включения его в готовые. `verify` ничего не исправляет и не пишет в пакет.

## 8. Точный план файлов

Изменить:

- `install/lab-provision`: help и ранний dispatch `release`, старые команды
  продолжают использовать прежний load_conf и код установки;
- `install/README.md`: краткая ссылка и отделение подготовки от применения;
- `docs/pilot-hardening/README.md`: ссылка на новый путь подготовки без замены
  действующего регламента обновления.

Добавить:

- `install/lab-release.py`: CLI, безопасный экспорт, manifest, locks, publish,
  offline verify/list, оркестрация только выделенных builders;
- `install/release-builder.py`: выполнение проверяемых сборочных шагов внутри
  builder, получение runtime fingerprint, wheels/lock и безопасных smoke checks;
- `install/lab-release-build.json.example`: несекретный профиль builders;
- `install/RELEASES.md`: команды оператора, recovery, требования и граница доверия;
- `install/tests/test_release.py`: целостность, безопасность путей, публикация,
  гонки, прерывания, совместимость dispatch и вложенного Slot manifest;
- `docs/release-preparation/{TASK.md,PLAN.md,RESULT.md}`; APPROVED и REVIEW.md
  добавляются уполномоченными участниками на соответствующих этапах.

Не менять `slot/release.py`, `slot/release.json`, units, приложения,
requirements/constraints, SQL/SQLite и установку Agent. Не добавлять apply,
общий переключаемый symlink, миграции, рестарты, enable и смену рабочих mounts.
Если для реализации окажется необходим иной контракт, сначала поправить PLAN
и получить одобрение поправки, не расширять область молча.

## 9. Проверки и отчётность

После одобрения и реализации локально:

```sh
bash -n install/lab-provision
python3 -m compileall -q install/lab-release.py install/release-builder.py
/workspace/gns-dev-venv/bin/python -m pytest -q install/tests/test_release.py
PYTHONPATH=slot /workspace/gns-dev-venv/bin/python -m pytest -q slot/tests
PYTHONPATH=manager /workspace/gns-dev-venv/bin/python -m pytest -q manager/tests
/workspace/gns-dev-venv/bin/python -m pytest -q agent/tests
git diff --check
```

Конкретно проверить: export выбранного SHA игнорирует dirty/untracked данные;
изменение/удаление/добавление, включая зависимости, неизвестное расширение и
symlink; повреждённый manifest и несовпадающий nested Slot manifest; обход путей;
запрет secrets/state; повторный ID, включая пустой final; два настоящих процесса
на один ID; инъекции ошибок и SIGKILL до/после publish; неизменность старого пакета;
отказ NOREPLACE unsupported; отсутствие production pct/systemctl/migrate.
Подмена builder допускается только в тестах; это моделирование оркестрации,
а не подтверждение runtime совместимости.

Требуют настоящих выделенных CephFS/LXC/PVE и указываются как НЕ ВЫПОЛНЕНО,
если инфраструктура недоступна:

- два клиента CephFS: mkdir lock и renameat2 NOREPLACE, crash recovery,
  ENOSPC/обрыв связи, отсутствие частичного final и проверка fsync;
- настоящие Ubuntu 24.04/Python 3.12 и Debian/PVE target профили, ABI/arch,
  скачивание wheels и установка с хешами в offline режиме;
- RO mounts, host root/mapped UID, ACL, отсутствие записи из учебного CT;
- imports и Slot check из точных guest путей после физической публикации;
- аудит состояния рабочих CT/сервисов до и после, подтверждающий отсутствие
  воздействия. Рабочий стенд для этих тестов не изменять без отдельного поручения.

В RESULT.md — commit реализации, команды и итоги, неизменённые контракты,
невыполненные стендовые проверки и отклонения от утверждённой версии PLAN.
Затем PR для независимого ревью; merge и развёртывание не выполнять.

## 10. Решения для одобрения и внешние зависимости

На одобрение вынесены: структура `<root>/<id>/{manager,slot,agent}`, сохранение
существующего Slot manifest как сверяемого подмножества, dedicated PVE/LXC
builders, wheelhouse Agent вместо переносимого host venv, обязательный
RENAME_NOREPLACE без небезопасного fallback и новый несекретный JSON-конфиг.
Публикация ничего не меняет в SLOT_CODE_REVISION/agent_code_dir/LAB_CODE_DIR.

Для реализации не нужны production VMID или секреты. Для настоящей сборки и
стендовой приёмки понадобятся выделенный сборочный узел, builder VMID и
отпечатки шаблонов, точный профиль Debian/Python/arch целевых узлов, доступ к
проверенному индексу wheels, подтверждённая поддержка NOREPLACE на CephFS,
заявления сопровождающего о state-совместимости. При отсутствии этих входных
данных утилита должна отказать, а RESULT — перечислить ограничения.

Применение пакета, соответствие полной структуры нынешнему селектору Slot,
локальная установка Agent из wheelhouse и порядок смены источников Manager/
Slot требуют отдельного согласованного этапа. Успех build/verify этого не обещает.
