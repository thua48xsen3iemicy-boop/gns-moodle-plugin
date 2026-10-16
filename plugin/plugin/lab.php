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
 * Full screen lab page: the topology viewer without the Moodle page chrome.
 *
 * @package    mod_gns3lab
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

require('../../config.php');

$id = required_param('id', PARAM_INT);

[$course, $cm] = get_course_and_cm_from_cmid($id, 'gns3lab');
$instance = $DB->get_record('gns3lab', ['id' => $cm->instance], '*', MUST_EXIST);

require_login($course, true, $cm);
$context = context_module::instance($cm->id);
require_capability('mod/gns3lab:view', $context);

$PAGE->set_url('/mod/gns3lab/lab.php', ['id' => $cm->id]);
$PAGE->set_pagelayout('embedded');
$PAGE->add_body_class('gns3lab-fullscreen');
$PAGE->set_title(format_string($instance->name));
$PAGE->set_heading(format_string($course->fullname));
$PAGE->set_activity_record($instance);

$viewer = new \mod_gns3lab\output\viewer($cm, $instance, true);

echo $OUTPUT->header();
echo $OUTPUT->render_from_template('mod_gns3lab/viewer', $viewer->export_for_template($OUTPUT));
echo $OUTPUT->footer();
