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
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
// GNU General Public License for more details.

/**
 * Standalone demo page. It does not access labs or sessions.
 *
 * @package    local_gns3isoflow
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

require_once(__DIR__ . '/../../config.php');

require_login();

$PAGE->set_url(new moodle_url('/local/gns3isoflow/index.php'));
$PAGE->set_context(context_system::instance());
$PAGE->set_pagelayout('standard');
$PAGE->set_title(get_string('pagetitle', 'local_gns3isoflow'));
$PAGE->set_heading(get_string('pagetitle', 'local_gns3isoflow'));

$demo = file_get_contents(__DIR__ . '/demo/ospf-multi-area.gns3');
if ($demo === false) {
    throw new moodle_exception('filenotfound');
}
$doc = json_decode($demo, true, 512, JSON_THROW_ON_ERROR);
$PAGE->requires->js_call_amd('local_gns3isoflow/app', 'init', [['demo' => $doc]]);

echo $OUTPUT->header();
echo html_writer::div('', 'gns3isoflow-alpha', ['id' => 'gns3isoflow-alpha-root']);
echo $OUTPUT->footer();
