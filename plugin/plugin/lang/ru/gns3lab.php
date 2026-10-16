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
$string['demonotice'] = 'Менеджер лаб ещё не подключён, поэтому здесь демо-лаба: топология OSPF с фактами об устройствах. Переключайте слои L1, L2, L3 и OSPF и попробуйте конфиг с ошибками.';
$string['demosource'] = 'демо';
$string['details'] = 'Сведения';
$string['drop'] = 'Отпустите файл .gns3';
$string['facts'] = 'Демо-факты из лабы';
$string['facts_help'] = 'Что бэкенд прислал бы о запущенной лабе: интерфейсы, VLAN, адреса, соседства (здесь — два демо-набора)';
$string['factsbroken'] = 'Конфиг с ошибками';
$string['factsok'] = 'Конфиг верный';
$string['fit'] = 'Вписать';
$string['fit_help'] = 'Вписать всю схему (клавиша 0)';
$string['gatewayurl'] = 'Адрес шлюза консолей';
$string['gatewayurl_desc'] = 'Браузер подключается к шлюзу консолей напрямую, например wss://gw.example.org. Пока не используется.';
$string['gns3lab:addinstance'] = 'Добавлять лабу GNS3';
$string['gns3lab:observe'] = 'Смотреть лабы студентов (только чтение)';
$string['gns3lab:use'] = 'Запускать свою лабу и работать в консолях';
$string['gns3lab:view'] = 'Просматривать лабу GNS3';
$string['hint'] = 'Тяните — сдвиг · щипок или колесо — масштаб · двойной тап — ближе · тап по объекту — сведения';
$string['httptimeout'] = 'Таймаут HTTP';
$string['httptimeout_desc'] = 'Таймаут запросов к менеджеру лаб, в секундах.';
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
$string['managersecret'] = 'Секрет менеджера лаб';
$string['managersecret_desc'] = 'Общий секрет для запросов к менеджеру лаб сервер-сервер. Никогда не уходит в браузер. Пока не используется.';
$string['managerurl'] = 'Адрес менеджера лаб';
$string['managerurl_desc'] = 'Базовый адрес API менеджера лаб, например https://labs.example.org. Пока не используется: активности показывают демо-лабу.';
$string['maxduration'] = 'Лимит сессии, минут';
$string['maxduration_help'] = 'Сколько может работать лаба студента. 0 — по умолчанию менеджера.';
$string['maxdurationinvalid'] = 'Лимит не может быть отрицательным.';
$string['modulename'] = 'Лаба GNS3';
$string['modulename_help'] = 'Активность «Лаба GNS3» показывает схему лабы и даёт студентам работать в консолях устройств прямо на странице курса.';
$string['modulenameplural'] = 'Лабы GNS3';
$string['openfile'] = 'Открыть .gns3';
$string['openfullscreen'] = 'Открыть лабу на весь экран';
$string['pluginadministration'] = 'Управление лабой GNS3';
$string['pluginname'] = 'Лаба GNS3';
$string['privacy:metadata:gns3labmanager'] = 'Менеджер лаб запускает лабу студента и хранит его сохранённый проект.';
$string['privacy:metadata:gns3labmanager:courseid'] = 'ID курса активности.';
$string['privacy:metadata:gns3labmanager:instanceid'] = 'ID активности, чтобы найти проект студента.';
$string['privacy:metadata:gns3labmanager:userid'] = 'ID пользователя, чтобы запускать и хранить его лабу.';
$string['readonly'] = 'Только просмотр';
$string['readonly_help'] = 'Вьюер ничего не меняет в лабе';
$string['topology'] = 'Схема топологии';
$string['zoom'] = 'Масштаб';
$string['zoomin'] = 'Увеличить';
$string['zoomout'] = 'Уменьшить';
