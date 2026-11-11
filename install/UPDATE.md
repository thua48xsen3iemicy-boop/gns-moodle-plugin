# Шпаргалка: как применить изменения из репозитория на стенде

Плагин Moodle здесь не описан: его ставят отдельно. Подробности шагов — [`README.md`](README.md).

```sh
P=/mnt/pve/cfs/gns3-ro/install/lab-provision
cd /mnt/pve/cfs/gns3-ro
```

## 1. Что изменилось с прошлого обновления стенда

Метка `stand` — коммит, который сейчас стоит на стенде (локальный тег в этом checkout, в GitHub не уходит).

```sh
git fetch && git diff --stat stand..origin/main -- manager slot agent constraints-tested.txt
git log --oneline stand..origin/main          # в сообщениях: версии, миграции, новые поля
```

Первый раз метки нет: `git tag stand HEAD` до `git pull` (стенд стоит на текущем HEAD).

## 2. Остановить работу

Без живых лаб (менеджер 0.20.0 и новее):

```sh
pct exec <MANAGER_VMID> -- runuser -u lab-manager -- /srv/gns3-ro/manager/lab-manager-admin maintenance on --in 5m --message "Обновление стенда"
pct exec <MANAGER_VMID> -- runuser -u lab-manager -- /srv/gns3-ro/manager/lab-manager-admin maintenance wait
```

## 3. Обновить код и выполнить шаги

```sh
git pull
```

| Что изменилось | Что запустить | Где |
|---|---|---|
| `manager/**` (код, миграции, шлюз `manager/gateway/`) | `$P manager` — миграции, `gateway.env`, перезапуск менеджера и шлюза, nginx | нода менеджера |
| `manager/requirements.txt`, `manager/gateway/requirements.txt`, `constraints-tested.txt` | сначала `$P venv manager`, потом `$P manager` | нода менеджера |
| `secrets/manager/images.json`, `sites.json`, `slots.json`, `manager.env.local` | `$P manager` | нода менеджера |
| `slot/**` — код | ничего: слот монтирует `gns3-ro/slot` при каждом запуске лабы, новый код берётся со следующей лабы | — |
| `slot/requirements.txt`, unit или `lab-slot-init` слота | `$P venv slot` (при новых зависимостях), `$P slot`, затем `$P enable` | каждая нода со слотами |
| `agent/**` | `$P agent` | каждая нода со слотами |
| только `plugin/`, `docs/`, `tools/` | на стенде ничего | — |

В конце всегда:

```sh
$P check                                     # на ноде менеджера
pct exec <MANAGER_VMID> -- runuser -u lab-manager -- /srv/gns3-ro/manager/lab-manager-admin maintenance off
git tag -f stand HEAD                        # запомнить, что теперь на стенде
```

`$P manager` идемпотентен: если не уверены, что нужно, — запускайте его, вреда не будет.
