<?php
// This file is part of Moodle - https://moodle.org/
//
// Moodle is free software: you can redistribute it and/or modify
// it under the terms of the GNU General Public License as published by
// the Free Software Foundation, either version 3 of the License, or
// (at your option) any later version.
//
// Moodle is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
// GNU General Public License for more details.
//
// You should have received a copy of the GNU General Public License
// along with Moodle.  If not, see <https://www.gnu.org/licenses/>.

/**
 * Russian strings for mod_gns3lab.
 *
 * @package    mod_gns3lab
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

defined('MOODLE_INTERNAL') || die();

$string['backtoactivity'] = 'Назад, на страницу активности';
$string['cannotobserveuser'] = 'Нельзя смотреть лабу этого пользователя: он не в ваших группах или не может работать в этой активности.';
$string['consoleclose'] = 'Закрыть консоль';
$string['consoleconnected'] = 'Подключено. Нажмите Enter, чтобы получить приглашение.';
$string['consoleconnecting'] = 'Подключение к консоли…';
$string['consolecopied'] = 'Скопировано';
$string['consolecopyfailed'] = 'Браузер не дал скопировать';
$string['consoleerror'] = 'Консоль недоступна.';
$string['consolekeys'] = 'Клавиши';
$string['consoleneedsrunning'] = 'Запустите лабу, чтобы открыть консоли устройств.';
$string['consolenodestopped'] = 'Нода остановлена. Консоль подключится сама, когда нода снова запустится.';
$string['consolenone'] = 'У этого устройства нет консоли.';
$string['consolenotsupported'] = 'Пока открываются только текстовые консоли.';
$string['consolereadonly'] = 'Только просмотр: ввод отключён.';
$string['consolereconnect'] = 'Подключиться снова';
$string['consolereconnected'] = 'Переподключено. Нажмите Enter, чтобы получить приглашение.';
$string['consolereconnecting'] = 'Связь потеряна, переподключение через {$a} с…';
$string['consoles'] = 'Консоли устройств';
$string['consolesnippets'] = 'Сниппеты';
$string['consoletabs'] = 'Открытые консоли';
$string['consolevnclater'] = 'Графическая консоль появится позже.';
$string['demonotice'] = 'У активности ещё нет эталонной лабы на менеджере, поэтому здесь демо-лаба: топология OSPF с фактами об устройствах. Переключайте слои L1, L2, L3 и OSPF и попробуйте конфиг с ошибками.';
$string['demosource'] = 'демо';
$string['details'] = 'Сведения';
$string['drop'] = 'Отпустите файл .gns3';
$string['facts'] = 'Демо-факты из лабы';
$string['facts_help'] = 'Что бэкенд прислал бы о запущенной лабе: интерфейсы, VLAN, адреса, соседства (здесь — два демо-набора)';
$string['factsbroken'] = 'Конфиг с ошибками';
$string['factsok'] = 'Конфиг верный';
$string['fit'] = 'Вписать';
$string['fit_help'] = 'Вписать всю схему (клавиша 0)';
$string['gatewaynotconfigured'] = 'Не задан адрес шлюза консолей: обратитесь к администратору сайта.';
$string['gatewayurl'] = 'Адрес шлюза консолей';
$string['gatewayurl_desc'] = 'Браузер подключается к шлюзу консолей напрямую, например wss://gw.example.org. Используется, если менеджер лаб не вернул адрес шлюза вместе с билетом консоли.';
$string['gns3lab:addinstance'] = 'Добавлять лабу GNS3';
$string['gns3lab:observe'] = 'Смотреть лабы студентов (только чтение)';
$string['gns3lab:use'] = 'Запускать свою лабу и работать в консолях';
$string['gns3lab:view'] = 'Просматривать лабу GNS3';
$string['hint'] = 'Тяните — сдвиг · щипок или колесо — масштаб · двойной тап — ближе · тап по объекту — сведения';
$string['httptimeout'] = 'Таймаут HTTP';
$string['httptimeout_desc'] = 'Таймаут запросов к менеджеру лаб, в секундах.';
$string['keybreak'] = 'Ctrl+Shift+6 — прервать команду (Cisco)';
$string['keyctrl'] = 'Ctrl для следующей клавиши';
$string['keyspace'] = 'Пробел — следующая страница --More--';
$string['labnotready'] = 'У активности ещё нет эталонной лабы: показана только демо-схема.';
$string['labref'] = 'Эталонная лаба';
$string['labref_help'] = 'Идентификатор эталонной лабы на менеджере (lab_ref). Оставьте пустым, чтобы показывать демо-лабу, пока менеджер не подключён.';
$string['labsettings'] = 'Лаба';
$string['layer_l1'] = 'L1 — физика: кабели и состояние линков (клавиша 1)';
$string['layer_l2'] = 'L2 — VLAN, транки и широковещательные домены (клавиша 2)';
$string['layer_l3'] = 'L3 — IP-подсети и адреса (клавиша 3)';
$string['layer_rt'] = 'Маршрутизация — соседства OSPF (клавиша 4)';
$string['layers'] = 'Слой схемы';
$string['livedata'] = 'Живые данные';
$string['livedata_help'] = 'Показать то, что шлюз получает из запущенной лабы: статусы нод и гостей Proxmox (здесь — демо-данные)';
$string['managererror'] = 'Менеджер лаб не смог выполнить запрос. Попробуйте позже.';
$string['managererror_gateway_unavailable'] = 'Шлюз консолей не ответил. Попробуйте через минуту.';
$string['managererror_lab_invalid'] = 'Эталонная лаба этой активности на менеджере сломана. Сообщите преподавателю.';
$string['managererror_lab_not_found'] = 'Менеджер лаб не знает эталонную лабу этой активности. Сообщите преподавателю.';
$string['managererror_node_not_found'] = 'Этого устройства нет в запущенной лабе.';
$string['managererror_session_conflict'] = 'У вас уже запущена другая лаба. Сначала остановите её.';
$string['managererror_session_not_found'] = 'Сессия лабы пропала. Обновите страницу.';
$string['managererror_session_not_running'] = 'Лаба не запущена.';
$string['managererror_unauthorized'] = 'Менеджер лаб не принял этот сайт: проверьте адрес и секрет менеджера в настройках плагина.';
$string['managernotconfigured'] = 'Менеджер лаб на этом сайте не подключён.';
$string['managersecret'] = 'Секрет менеджера лаб';
$string['managersecret_desc'] = 'Общий секрет для запросов к менеджеру лаб сервер-сервер. Никогда не уходит в браузер.';
$string['managerunreachable'] = 'Менеджер лаб недоступен. Попробуйте позже.';
$string['managerurl'] = 'Адрес менеджера лаб';
$string['managerurl_desc'] = 'Базовый адрес API менеджера лаб, например https://labs.example.org. Без него активности показывают демо-лабу.';
$string['maxduration'] = 'Лимит сессии, минут';
$string['maxduration_help'] = 'Сколько может работать лаба студента. 0 — по умолчанию менеджера.';
$string['maxdurationinvalid'] = 'Лимит не может быть отрицательным.';
$string['modulename'] = 'Лаба GNS3';
$string['modulename_help'] = 'Активность «Лаба GNS3» показывает схему лабы и даёт студентам работать в консолях устройств прямо на странице курса.';
$string['modulenameplural'] = 'Лабы GNS3';
$string['nodenotinsession'] = 'Этого устройства нет в запущенной лабе.';
$string['nolabref'] = 'У активности не выбрана эталонная лаба.';
$string['nosession'] = 'Нет сессии лабы, которую можно остановить.';
$string['openfile'] = 'Открыть .gns3';
$string['openfullscreen'] = 'Открыть лабу на весь экран';
$string['pluginadministration'] = 'Управление лабой GNS3';
$string['pluginname'] = 'Лаба GNS3';
$string['previewerror'] = 'Не удалось получить эталонную лабу у менеджера, вместо неё показана демо-лаба.';
$string['privacy:metadata:gns3labmanager'] = 'Менеджер лаб запускает лабу студента и хранит его сохранённый проект.';
$string['privacy:metadata:gns3labmanager:courseid'] = 'ID курса активности.';
$string['privacy:metadata:gns3labmanager:instanceid'] = 'ID активности, чтобы найти проект студента.';
$string['privacy:metadata:gns3labmanager:userid'] = 'ID пользователя, чтобы запускать и хранить его лабу.';
$string['readonly'] = 'Только просмотр';
$string['readonly_help'] = 'Вьюер ничего не меняет в лабе';
$string['sessionnotrunning'] = 'Лаба не запущена.';
$string['startlab'] = 'Запустить лабу';
$string['status_failed'] = 'Лаба не запустилась';
$string['status_none'] = 'Лаба не запущена';
$string['status_queued'] = 'Ожидание свободного слота…';
$string['status_running'] = 'Лаба работает';
$string['status_starting'] = 'Запуск…';
$string['status_stopped'] = 'Лаба остановлена';
$string['status_stopping'] = 'Остановка…';
$string['stoplab'] = 'Остановить лабу';
$string['timeleft'] = 'Осталось {$a}';
$string['topology'] = 'Схема топологии';
$string['zoom'] = 'Масштаб';
$string['zoomin'] = 'Увеличить';
$string['zoomout'] = 'Уменьшить';
