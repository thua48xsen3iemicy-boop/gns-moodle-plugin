# Отчёт: выход лабы в интернет (узел «Интернет»)

Ветка `claude/laughing-maxwell-pf4w9j` (по заданию `feature/lab-uplink`, но сессия привязана к этой ветке). Задание — `docs/lab-uplink/TASK.md`
из ветки `claude/elegant-goodall-njbf35`. Коммиты по компонентам: install, slot 0.13.0, manager 0.13.0, регистрация выпуска, образ.

## Что сделано

**А. `install/`** — `INET_BRIDGE`/`INET_VLAN` (пустые — всё как раньше); `net1` в шаблоне без `firewall=1`; клон переписывает `net1`
без `hwaddr`; шаблон без `net1` отклоняется с подсказкой `pct set …`; `SLOT_UPLINK_MAC=02:6c:61:<VMID в 3 байтах>` в `slot.env`;
`lab-uplink-filter` (nftables, `bridge`; `--check`, `--install` для systemd-юнита); README. В `lab-provision` добавлена защита
`[[ $BASH_SOURCE != $0 ]] || main`, чтобы тесты подключали функции.

**Б. `slot/` 0.13.0** — `NodeSpec.uplink_port`; MAC порта (`mac_address` = `SLOT_UPLINK_MAC − uplink_port`, перенос через байты
допустим, первый байт не меняется); узел `cloud` `_uplink` с `ports_mapping` на `eth1` и линк; `uplink` в `features`; 422 без
возможности; `_uplink` и его линк скрыты в инвентаре, статусе, топологии, фактах, L1; при продолжении проекта не пересоздаются,
отсутствие — ошибка сверки. Контракт: §4.2, §7.

**В. `manager/` 0.13.0** — поле проходит в `NodeSpec` только если задано; проверка лабы и эталонной `ticket.json`; новые коды
`too_many_uplinks`, `uplink_port_in_use`, `unsupported_lab` (§2.5, §2.6); образ `lab-uplink` в `examples/images.json`.

**Г. `images/lab-uplink/` 0.1.0** — PPPoE-клиент, DHCP/DNS (`dnsmasq`), NAT и фильтр (`nftables`), MSS clamp, число LAN-портов
из числа адаптеров при загрузке, установщик с реквизитами из `--pppoe-user/--pppoe-password` (в Git только заглушка), README.

## Отличия от задания

1. **Выбор слота (В.2).** Менеджер сейчас не читает `capabilities` слотов, а свободный слот — выключенный LXC, спросить его нельзя.
   Поэтому возможность задаётся статически: `"features": ["uplink"]` в `slots.json` (`lab-provision secrets` пишет её при
   заданном `INET_BRIDGE`). Размещение берёт только такие слоты; если их нет вовсе — `409 unsupported_lab` при создании сессии.
   Слот в любом случае сам отвечает 422. Это новое поле конфигурации менеджера (`slots.json.example`, §7 контракта).
2. **Фильтр моста.** Сводится к портам `veth*i1` указанного моста; при vlan-aware мосте скрипт проверяет, что они в `INET_VLAN` как PVID,
   иначе отказывается применяться. Для не-vlan-aware моста Proxmox нужен мост `vmbrXvТЕГ` в `--bridge`.
3. Ветка: см. выше. Pre-existing `slot/tests` на python 3.13 не запускались: venv собран на 3.12 по `AGENTS.md`.

## Что не проверено

* Ничего не запускалось на Proxmox/GNS3/LXC. Сквозного теста с `pppoe-server` нет: в окружении нет `ip`, `pppd`, `pppoe-server`.
* Правила `lab-uplink-filter` проверены только как текст; `nft -c` их не принимает в этой среде (нет модуля ядра для `meta ibrname`),
  `nftables` образа `nft -c` проходят.
* Нумерация MAC адаптеров GNS3 2.2.61 (база + номер, перенос через байты) взята из `slot/api/facts.py:adapter_macs`.
* Падения `agent/tests` под root (9) — окружение, код агента не менялся.

## Результаты тестов (python 3.12, под root)

`manager/tests` 464 passed (с временным PostgreSQL), `slot/tests` 445 passed, `install/tests` 127 passed, `images/lab-uplink/tests` 22 passed,
`agent/tests` 9 failed — root, без изменений в агенте. `slot/release.py check` ok, `compileall slot manager` ok.

## Что проверить на стенде

Список владельца из `TASK.md` без изменений, плюс первым пунктом: **работает ли штатный `cloud` GNS3 2.2.61 на `eth1` в
непривилегированном LXC** (нужен сырой сокет). Дополнительно:

1. `nft -f` для `lab-uplink-filter --check` на узле Proxmox (нужен `nft_meta_bridge`); `nft list table bridge lab_uplink`.
2. `slots.json` менеджера: `features: ["uplink"]` у всех слотов с `net1`; лаба с `uplink_port` идёт только на них.
3. Узел «Интернет»: `pppd`, `dnsmasq`, `nft list ruleset` внутри гостя (`qga exec`); MAC внешнего порта = `SLOT_UPLINK_MAC`.
4. Факты слота для узла «Интернет» (`linux.net/1` собирается как для любого QEMU-узла) не мешают.
