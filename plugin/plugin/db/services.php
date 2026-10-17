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
 * External functions of mod_gns3lab, called by the activity page through core/ajax.
 *
 * @package    mod_gns3lab
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

defined('MOODLE_INTERNAL') || die();

$functions = [
    'mod_gns3lab_get_state' => [
        'classname' => \mod_gns3lab\external\get_state::class,
        'description' => 'Current lab session of the user or of an observed student.',
        'type' => 'read',
        'ajax' => true,
        'loginrequired' => true,
    ],
    'mod_gns3lab_start_session' => [
        'classname' => \mod_gns3lab\external\start_session::class,
        'description' => 'Starts the lab of the current user.',
        'type' => 'write',
        'ajax' => true,
        'loginrequired' => true,
    ],
    'mod_gns3lab_stop_session' => [
        'classname' => \mod_gns3lab\external\stop_session::class,
        'description' => 'Stops the lab of the current user.',
        'type' => 'write',
        'ajax' => true,
        'loginrequired' => true,
    ],
    'mod_gns3lab_get_console_ticket' => [
        'classname' => \mod_gns3lab\external\get_console_ticket::class,
        'description' => 'One-time ticket to open a node console through the console gateway.',
        'type' => 'write',
        'ajax' => true,
        'loginrequired' => true,
    ],
];
