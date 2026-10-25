<?php
// This file is part of Moodle - http://moodle.org/
//
// Moodle is free software: you can redistribute it and/or modify
// it under the terms of the GNU General Public License as published by
// the Free Software Foundation, either version 3 of the License, or
// (at your option) any later version.

/**
 * Theme configuration.
 *
 * @package theme_gnsportal
 * @license http://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

defined('MOODLE_INTERNAL') || die();

require_once(__DIR__ . '/lib.php');

$THEME->name = 'gnsportal';
$THEME->parents = ['boost'];
$THEME->sheets = ['loginlab'];
$THEME->javascripts_footer = ['loginlab'];
$THEME->editor_sheets = [];
$THEME->scss = function($theme) {
    return theme_gnsportal_get_main_scss_content($theme);
};
$THEME->layouts = [
    'login' => [
        'file' => 'login.php',
        'regions' => [],
        'options' => ['langmenu' => true],
    ],
];
$THEME->rendererfactory = 'theme_overridden_renderer_factory';
$THEME->iconsystem = \core\output\icon_system::FONTAWESOME;
