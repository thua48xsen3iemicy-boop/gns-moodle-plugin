<?php
// This file is part of Moodle - http://moodle.org/
//
// Moodle is free software: you can redistribute it and/or modify
// it under the terms of the GNU General Public License as published by
// the Free Software Foundation, either version 3 of the License, or
// (at your option) any later version.

/**
 * GNS Portal theme version.
 *
 * @package theme_gnsportal
 * @license http://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

defined('MOODLE_INTERNAL') || die();

$plugin->component = 'theme_gnsportal';
$plugin->version = 2026102500;
$plugin->requires = 2025092600;
$plugin->dependencies = ['theme_boost' => 2025100600];
$plugin->maturity = MATURITY_ALPHA;
$plugin->release = '0.1.0';
