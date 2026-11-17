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

namespace mod_gns3lab\external;

use core_external\external_api;
use core_external\external_function_parameters;
use core_external\external_multiple_structure;
use core_external\external_single_structure;
use core_external\external_value;
use mod_gns3lab\local\activity_sessions;
use mod_gns3lab\local\course_labs;
use mod_gns3lab\local\course_results;
use mod_gns3lab\local\lab_access;
use mod_gns3lab\local\manager_client;

/**
 * Students by labs of a course for the teacher page (course.php): who runs which lab and in which state.
 *
 * Only the sessions are here, as short summaries from one list per lab, with the final grade of the gradebook and a
 * summary of the auto-check runs. The topology and the facts of a student come from mod_gns3lab_get_state and
 * mod_gns3lab_get_facts, which check observe and groups on their own. The session id is not part of the answer.
 *
 * @package    mod_gns3lab
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
class get_course_overview extends external_api {
    /**
     * Parameters.
     *
     * @return external_function_parameters
     */
    public static function execute_parameters(): external_function_parameters {
        return new external_function_parameters([
            'courseid' => new external_value(PARAM_INT, 'Course id'),
            'groupid' => new external_value(PARAM_INT, 'Only the members of this group, 0 for all', VALUE_DEFAULT, 0),
            'cmid' => new external_value(PARAM_INT, 'Only this lab of the course, 0 for all', VALUE_DEFAULT, 0),
        ]);
    }

    /**
     * Returns the table of students by labs.
     *
     * @param int $courseid course id
     * @param int $groupid group, 0 for all
     * @param int $cmid one lab, 0 for all
     * @return array
     */
    public static function execute(int $courseid, int $groupid = 0, int $cmid = 0): array {
        global $DB;

        ['courseid' => $courseid, 'groupid' => $groupid, 'cmid' => $cmid] = self::validate_parameters(
            self::execute_parameters(),
            ['courseid' => $courseid, 'groupid' => $groupid, 'cmid' => $cmid]
        );
        $course = get_course($courseid);
        $coursecontext = \context_course::instance($course->id);
        self::validate_context($coursecontext);

        // Labs without mod/gns3lab:observe are not in the answer; none left means this is not the page of the user.
        $cms = course_labs::observable($course);
        if (!$cms) {
            throw new \required_capability_exception($coursecontext, 'mod/gns3lab:observe', 'nopermissions', '');
        }
        if ($groupid && !array_key_exists($groupid, course_labs::groups($course))) {
            throw new \invalid_parameter_exception('The group is not available to the user');
        }
        if ($cmid) {
            if (!isset($cms[$cmid])) {
                throw new \invalid_parameter_exception('The lab is not available to the user');
            }
            $cms = [$cmid => $cms[$cmid]];
        }
        $showgrades = course_results::can_see_grades($coursecontext);
        $settings = $DB->get_records_list(
            'gns3lab',
            'id',
            array_map(fn($cm) => (int) $cm->instance, array_values($cms)),
            '',
            'id, grade, checksenabled'
        );

        $configured = manager_client::is_configured();
        $client = $configured ? \core\di::get(manager_client::class) : null;
        $labs = [];
        $students = [];
        $cells = [];
        foreach ($cms as $cm) {
            $access = lab_access::from_cmid($cm->id);
            $found = course_labs::students($cm, $groupid);
            $lab = ['cmid' => $cm->id, 'name' => \core_external\util::format_string($cm->name, $cm->context), 'error' => ''];
            $sessions = [];
            if (!$configured) {
                $lab['error'] = 'notconfigured';
            } else if ($found) {
                $entry = activity_sessions::get($client, (int) $cm->instance);
                $lab['error'] = $entry->error;
                $sessions = $entry->sessions;
            }
            // The students are enrolled with mod/gns3lab:use already (course_labs::students), only the groups are left.
            $shared = $access->share_group(array_map('intval', array_keys($found)));
            // Grades and checks only of the students the user may observe in this lab: the average counts them alone.
            $results = course_results::grades(
                $course,
                $cm,
                $showgrades ? (int) ($settings[$cm->instance]->grade ?? 0) : 0,
                $shared
            );
            $checks = course_results::checks((int) $cm->instance, $shared, $showgrades);
            $labs[] = $lab + $results['lab'] + ['checks' => !empty($settings[$cm->instance]->checksenabled)];
            $shared = array_flip($shared);
            foreach ($found as $user) {
                $students[$user->id] = $user;
                $observable = isset($shared[(int) $user->id]);
                $cells[$cm->id][$user->id] = [
                    'cmid' => $cm->id,
                    'userid' => (int) $user->id,
                    'observable' => $observable,
                    'session' => $observable ? ($sessions[$user->id] ?? null) : null,
                    'grade' => $observable ? ($results['grades'][(int) $user->id] ?? null) : null,
                    'checks' => $observable ? ($checks[(int) $user->id] ?? null) : null,
                ];
            }
        }

        // A student the teacher cannot observe in any lab is not shown at all, not even the name.
        global $PAGE;
        $output = $PAGE->get_renderer('core');
        $list = [];
        $allcells = [];
        foreach ($students as $user) {
            $row = [];
            foreach ($cms as $cm) {
                $row[] = $cells[$cm->id][$user->id] ?? [
                    'cmid' => $cm->id, 'userid' => (int) $user->id, 'observable' => false,
                    'session' => null, 'grade' => null, 'checks' => null,
                ];
            }
            if (!array_filter($row, fn($cell) => $cell['observable'])) {
                continue;
            }
            $list[] = [
                'userid' => (int) $user->id,
                'fullname' => fullname($user),
                'pictureurl' => (new \user_picture($user))->get_url($PAGE, $output)->out(false),
            ];
            array_push($allcells, ...$row);
        }
        return ['showgrades' => $showgrades, 'labs' => $labs, 'students' => $list, 'cells' => $allcells];
    }

    /**
     * Result.
     *
     * @return external_single_structure
     */
    public static function execute_returns(): external_single_structure {
        return new external_single_structure([
            'showgrades' => new external_value(PARAM_BOOL, 'The user may see the grades of the gradebook'),
            'labs' => new external_multiple_structure(new external_single_structure([
                'cmid' => new external_value(PARAM_INT, 'Course module id'),
                'name' => new external_value(PARAM_TEXT, 'Activity name'),
                'error' => new external_value(PARAM_ALPHANUMEXT, 'Why the lab manager gave nothing for this lab, empty if it did'),
            ] + course_results::lab_fields()), 'Labs of the course the user can observe, in the order of the course'),
            'students' => new external_multiple_structure(new external_single_structure([
                'userid' => new external_value(PARAM_INT, 'User id'),
                'fullname' => new external_value(PARAM_TEXT, 'Full name'),
                'pictureurl' => new external_value(PARAM_URL, 'Small profile picture'),
            ]), 'Students who can use at least one of the labs and whom the user can observe in at least one'),
            'cells' => new external_multiple_structure(new external_single_structure([
                'cmid' => new external_value(PARAM_INT, 'Course module id'),
                'userid' => new external_value(PARAM_INT, 'User id'),
                'observable' => new external_value(PARAM_BOOL, 'The user may observe this student in this lab'),
                'session' => \mod_gns3lab\local\session_data::summary_structure(),
                'grade' => course_results::grade_structure(),
                'checks' => course_results::checks_structure(),
            ]), 'One cell per student and lab'),
        ]);
    }
}
