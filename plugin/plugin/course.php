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
 * Labs of a course for the teacher: a summary of the grades by students and labs, one lab with the live state of the sessions
 * of its students, and a card per student and lab.
 *
 * @package    mod_gns3lab
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

require('../../config.php');

$id = required_param('id', PARAM_INT);
// The lab shown on its own; 0 is the summary of all labs. The page follows it in the address while the teacher switches.
$labid = optional_param('lab', 0, PARAM_INT);

$course = get_course($id);
require_login($course);
$context = context_course::instance($course->id);

// The labs of the page are the visible activities of the course that the user may observe; none means no page.
if (!\mod_gns3lab\local\course_labs::has_observable($course)) {
    throw new required_capability_exception($context, 'mod/gns3lab:observe', 'nopermissions', '');
}

$PAGE->set_url('/mod/gns3lab/course.php', ['id' => $course->id] + ($labid ? ['lab' => $labid] : []));
$PAGE->set_pagelayout('embedded');
$PAGE->add_body_class('gns3lab-fullscreen');
$PAGE->set_title(format_string($course->shortname, true, ['context' => $context]) . ': ' . get_string('courselabs', 'gns3lab'));
$PAGE->set_heading(format_string($course->fullname, true, ['context' => $context]));

$overview = new \mod_gns3lab\output\course_overview($course, $labid);

echo $OUTPUT->header();
echo \mod_gns3lab\local\maintenance::notification();
echo $OUTPUT->render_from_template('mod_gns3lab/course', $overview->export_for_template($OUTPUT));
echo $OUTPUT->footer();
