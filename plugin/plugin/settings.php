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
 * Site settings for mod_gns3lab.
 *
 * Without the lab manager URL and secret activities show the bundled demo topology.
 *
 * @package    mod_gns3lab
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

defined('MOODLE_INTERNAL') || die();

if ($ADMIN->fulltree) {
    $settings->add(new admin_setting_configtext(
        'gns3lab/managerurl',
        new lang_string('managerurl', 'gns3lab'),
        new lang_string('managerurl_desc', 'gns3lab'),
        '',
        PARAM_URL
    ));
    $settings->add(new admin_setting_configpasswordunmask(
        'gns3lab/managersecret',
        new lang_string('managersecret', 'gns3lab'),
        new lang_string('managersecret_desc', 'gns3lab'),
        ''
    ));
    $settings->add(new admin_setting_configtext(
        'gns3lab/gatewayurl',
        new lang_string('gatewayurl', 'gns3lab'),
        new lang_string('gatewayurl_desc', 'gns3lab'),
        '',
        PARAM_RAW_TRIMMED
    ));
    $settings->add(new admin_setting_configtext(
        'gns3lab/httptimeout',
        new lang_string('httptimeout', 'gns3lab'),
        new lang_string('httptimeout_desc', 'gns3lab'),
        10,
        PARAM_INT
    ));
}
