# Lab Slot — prototype 0.1

**Назначение:** один LXC-слот управляет лабораторией через GNS3 2.2.61. Это не плагин Moodle и не Lab Manager. В Slot не передаются сведения о студенте или Proxmox.

## Размещение

В репозитории находятся только файлы `slot/` (без повторного `slot/slot/`). На всех LXC смонтировать репозиторий в **одинаковую** точку `/srv/gns3-ro` с доступом **RO**. Файлы `slot/main.py`, `slot/api/`, `slot/venv/` и `slot/lab-slot.service` находятся на общей CephFS. Системный `gns3.service` уже установлен и запускается от пользователя `gns3`.

Рабочие данные (CephFS RW) остаются в `/opt/gns3/projects`, общие QEMU/IOL-образы — в `/opt/gns3/images/{QEMU,IOU}` (допускаются символические ссылки на RO CephFS). Сокеты QGA создаются под `/opt/gns3/qga` внутри LXC.

**Важно:** `slot/venv/` собирается администратором по **тому же абсолютному пути**, в котором затем монтируется для LXC, под Ubuntu 24.04 и одинаковую версию Python/архитектуру. Не изменяйте окружение работающих слотов: версия/релиз и управляемая смена при остановленном слоте. Каталог venv не включайте в Git.

## Установка Python-окружения (на машине с RW-доступом к CephFS)

```sh
cd /srv/gns3-ro/slot
python3 -m venv venv
venv/bin/python -m pip install -r requirements.txt
```

При необходимости создавайте временное окружение для сборки и затем атомарно заменяйте версии; `venv` нельзя бездумно переносить по разным абсолютным путям. На рабочих LXC CephFS — только RO.

## Подключение systemd (на каждом LXC, однократно)

```sh
sudo install -d -m 0700 /etc/lab-slot
sudo cp /srv/gns3-ro/slot/slot.env.example /etc/lab-slot/slot.env
sudo sed -i "s/REPLACE_WITH_LONG_RANDOM_SECRET/$(openssl rand -hex 32)/" /etc/lab-slot/slot.env
sudo chown root:root /etc/lab-slot/slot.env
sudo chmod 600 /etc/lab-slot/slot.env
sudo systemctl link /srv/gns3-ro/slot/lab-slot.service
sudo systemctl daemon-reload
sudo systemctl enable --now lab-slot.service
sudo systemctl status lab-slot.service
```

Администратор должен обеспечить доступность mount до старта systemd. При изменении unit на **работающем** LXC: `systemctl daemon-reload` и, если требуется, `systemctl restart lab-slot`. При следующей загрузке остановленного LXC unit читается заново. `SLOT_API_TOKEN` **не хранится на общей RO CephFS** и не попадает в Git.

FastAPI слушает только `127.0.0.1:8080`. Для удалённого Lab Manager позднее используется TLS-шлюз с **mTLS**; не открывайте порт наружу без защиты. Для ручного теста:

```sh
TOKEN=$(sed -n 's/^SLOT_API_TOKEN=//p' /etc/lab-slot/slot.env)
curl -s http://127.0.0.1:8080/healthz
curl -s -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8080/v1/readyz
```

## Проверка первой лаборатории

Убедитесь, что образ Debian 12 без повреждённой backing chain и Cisco IOL видны GNS3 в штатных путях. Сервис по умолчанию **не запускает VM автоматически** (ресурсы 10 GiB LXC могут быть ограничены).

```sh
TOKEN=$(sed -n 's/^SLOT_API_TOKEN=//p' /etc/lab-slot/slot.env)
cd /srv/gns3-ro/slot
curl -sS -X POST http://127.0.0.1:8080/v1/labs/deploy \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  --data-binary @examples/cisco-3x2.json | python3 -m json.tool

# Поставьте project_id из ответа:
PID=YOUR-PROJECT-UUID
curl -sS -X POST -H "Authorization: Bearer $TOKEN" \
  "http://127.0.0.1:8080/v1/labs/$PID/start"
curl -sS -H "Authorization: Bearer $TOKEN" \
  "http://127.0.0.1:8080/v1/labs/$PID/topology" | python3 -m json.tool
```

Запрос `deploy` с **тем же `project_id`** открывает существующий проект без перезаписи его рабочих файлов. Для безопасных повторов Manager должен сохранять и передавать `project_id` (из первого ответа) при последующих запусках. Экземпляр проекта нельзя одновременно использовать в двух слотах.

## Доступные маршруты (v0.1)

| Метод | Маршрут | Действие |
|---|---|---|
| GET | `/healthz` | liveness, без токена |
| GET | `/v1/readyz` | версия GNS3, токен |
| GET | `/v1/labs` | список проектов |
| POST | `/v1/labs/deploy` | создать топологию из JSON; с прежним UUID — возобновить без перезаписи |
| GET | `/v1/labs/{project_id}` | проект и узлы |
| GET | `/v1/labs/{project_id}/topology` | узлы + связи |
| POST | `/v1/labs/{project_id}/{open,start,stop,close}` | управление жизненным циклом |
| POST | `/v1/labs/{project_id}/nodes/{node_id}/{start,stop}` | одна нода |
| GET | `/v1/labs/{project_id}/nodes/{node_id}/console` | номер и тип консоли |
| POST | `/v1/labs/{project_id}/nodes/{node_id}/qga/exec` | выполнить команду в QEMU через QGA |
| POST | `/v1/labs/{project_id}/nodes/{node_id}/serial/enable` | включить getty на ttyS0 через QGA |
| WS | `/v1/console/{project_id}/{node_id}` | поток консоли QEMU/Cisco (Telnet GNS3) |

Обычные HTTP-маршруты принимают `Authorization: Bearer <SLOT_API_TOKEN>`. Browser WebSocket использует `new WebSocket(url, ['lab-slot', 'bearer.' + token])`: токен отправляется как субпротокол, не в query URL. Поток WS двоичный; используйте `TextDecoder` и `Uint8Array` при связывании с xterm.js. В этом прототипе TLS защищается внешним шлюзом, а порт Uvicorn доступен только локально. QEMU serial-консоль должна быть запущена (`serial-getty@ttyS0`), иначе Telnet откроется без login prompt. Cisco CLI работает через обычную GNS3 Telnet-консоль.

## В границах первого прототипа

Реализованы API вызовы, подготовка 3 Cisco IOL + 6 Debian QEMU, QGA-команды и WS↔Telnet bridge. Запуск sidecar L1 keepalive, метрики, автопроверки, Node Wipe и безопасная атомарная подготовка/восстановление `.gns3` из эталонных каталогов в **эту версию не включены**. Проект, не завершивший deploy из-за ошибки, остаётся для диагностики; автоматическое удаление рабочих данных отсутствует. При ошибке развертывания перезапуск выполняйте с тем же `project_id` только после проверки состояния; частичные проекты требуют отдельной очистки оператором.

Защита: пока только `Bearer` на loopback, затем mTLS. В вызове `qga/exec` выполняются доверенные команды менеджера (по сути root в Debian), поэтому выдавать этот маршрут непосредственно Moodle, студентам или публичному прокси нельзя. При публикации через reverse proxy запретите публикацию `/docs` и `/openapi.json` или защитите их тем же доступом.
