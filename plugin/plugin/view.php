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
 * Activity page: the description and a button to open the lab full screen.
 *
 * @package    mod_gns3lab
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

require('../../config.php');
require_once($CFG->libdir . '/completionlib.php');

$id = required_param('id', PARAM_INT);
$action = optional_param('action', '', PARAM_ALPHA);

[$course, $cm] = get_course_and_cm_from_cmid($id, 'gns3lab');
$instance = $DB->get_record('gns3lab', ['id' => $cm->instance], '*', MUST_EXIST);

require_login($course, true, $cm);
$context = context_module::instance($cm->id);
require_capability('mod/gns3lab:view', $context);

$event = \mod_gns3lab\event\course_module_viewed::create([
    'objectid' => $instance->id,
    'context' => $context,
]);
$event->add_record_snapshot('course', $course);
$event->add_record_snapshot('gns3lab', $instance);
$event->trigger();

$completion = new completion_info($course);
$completion->set_module_viewed($cm);

$PAGE->set_url('/mod/gns3lab/view.php', ['id' => $cm->id]);
$PAGE->set_title(format_string($course->shortname) . ': ' . format_string($instance->name));
$PAGE->set_heading(format_string($course->fullname));
$PAGE->set_activity_record($instance);

if ($action === 'wipe') {
    // The student wipes the own lab in this activity (manager CONTRACTS 2.10): confirm, then delete at once.
    $client = \core\di::get(\mod_gns3lab\local\manager_client::class);
    if (!\mod_gns3lab\local\purges::can_wipe($context, $client)) {
        throw new \moodle_exception('wipeunavailable', 'gns3lab', $PAGE->url);
    }
    if (!optional_param('confirm', 0, PARAM_BOOL)) {
        $continue = new \core\url($PAGE->url, ['action' => 'wipe', 'confirm' => 1, 'sesskey' => sesskey()]);
        echo $OUTPUT->header();
        echo $OUTPUT->confirm(get_string('wipeconfirm', 'gns3lab'), $continue, $PAGE->url);
        echo $OUTPUT->footer();
        exit;
    }
    require_sesskey();
    try {
        $result = \mod_gns3lab\local\purges::wipe($instance, (int) $USER->id, $client);
    } catch (\mod_gns3lab\local\manager_exception $e) {
        redirect($PAGE->url, get_string('wipefailed', 'gns3lab', $e->remotecode), null,
            \core\output\notification::NOTIFY_ERROR);
    } catch (\moodle_exception $e) {
        redirect($PAGE->url, $e->getMessage(), null, \core\output\notification::NOTIFY_WARNING);
    }
    $message = !$result->workspaces ? 'wipenothing' : ($result->pending ? 'wipepending' : 'wipedone');
    redirect($PAGE->url, get_string($message, 'gns3lab'), null, \core\output\notification::NOTIFY_SUCCESS);
}

$overview = new \mod_gns3lab\output\overview($cm, $instance);

echo $OUTPUT->header();
echo $OUTPUT->render_from_template('mod_gns3lab/overview', $overview->export_for_template($OUTPUT));
echo $OUTPUT->footer();
