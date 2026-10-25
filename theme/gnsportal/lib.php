<?php
// This file is part of Moodle - http://moodle.org/
//
// Moodle is free software: you can redistribute it and/or modify
// it under the terms of the GNU General Public License as published by
// the Free Software Foundation, either version 3 of the License, or
// (at your option) any later version.

/**
 * Stylesheet compilation for the portal theme.
 *
 * @package theme_gnsportal
 * @license http://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

defined('MOODLE_INTERNAL') || die();

require_once($CFG->dirroot . '/theme/boost/lib.php');

/**
 * Compile Boost with the portal palette and component styles.
 *
 * @param theme_config $theme Theme configuration.
 * @return string SCSS content.
 */
function theme_gnsportal_get_main_scss_content($theme): string {
    $scss = <<<'SCSS'
$primary: #1f6fc5;
$success: #1e8e4f;
$warning: #ad7f08;
$danger: #c8423a;
$body-bg: #e8ecef;
$body-color: #16212b;
$font-family-sans-serif: "IBM Plex Sans", system-ui, -apple-system, "Segoe UI", sans-serif;
$font-family-monospace: "IBM Plex Mono", ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
$border-radius: .45rem;
$border-radius-lg: .6rem;
SCSS;
    $scss .= "\n" . theme_boost_get_main_scss_content($theme);
    $scss .= "\n" . file_get_contents(__DIR__ . '/scss/fonts.scss');
    $scss .= "\n" . file_get_contents(__DIR__ . '/scss/portal.scss');
    return $scss;
}
