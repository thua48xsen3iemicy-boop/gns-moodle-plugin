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
 * Library of interface functions for mod_gns3lab.
 *
 * @package    mod_gns3lab
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

/**
 * Declares which Moodle features the module supports.
 *
 * @param string $feature FEATURE_xx constant
 * @return mixed true/false for supported features, a value for FEATURE_MOD_PURPOSE, null if unknown
 */
function gns3lab_supports($feature) {
    return match ($feature) {
        FEATURE_MOD_INTRO => true,
        FEATURE_SHOW_DESCRIPTION => true,
        FEATURE_COMPLETION_TRACKS_VIEWS => true,
        FEATURE_GROUPS => true,
        FEATURE_GROUPINGS => true,
        FEATURE_GRADE_HAS_GRADE => true,
        FEATURE_GRADE_OUTCOMES => false,
        FEATURE_BACKUP_MOODLE2 => false,
        FEATURE_MOD_PURPOSE => MOD_PURPOSE_INTERACTIVECONTENT,
        default => null,
    };
}

/**
 * Creates a new activity instance.
 *
 * @param stdClass $data data from the module form
 * @param mod_gns3lab_mod_form|null $mform the form
 * @return int new instance id
 */
function gns3lab_add_instance(stdClass $data, $mform = null): int {
    global $DB;

    $data->timecreated = time();
    $data->timemodified = $data->timecreated;
    $data->labversionid = (int) ($data->labversionid ?? 0);
    $data->labref = $data->labversionid ? '' : trim($data->labref ?? '');
    $data->maxduration = (int) ($data->maxduration ?? 0);
    gns3lab_prepare_view_settings($data);
    gns3lab_prepare_checks_settings($data);
    $data->id = $DB->insert_record('gns3lab', $data);
    gns3lab_grade_item_update($data);
    return $data->id;
}

/**
 * Updates an activity instance.
 *
 * @param stdClass $data data from the module form
 * @param mod_gns3lab_mod_form|null $mform the form
 * @return bool true on success
 */
function gns3lab_update_instance(stdClass $data, $mform = null): bool {
    global $DB;

    $data->id = $data->instance;
    $data->timemodified = time();
    $data->labversionid = (int) ($data->labversionid ?? 0);
    $data->labref = $data->labversionid ? '' : trim($data->labref ?? '');
    $data->maxduration = (int) ($data->maxduration ?? 0);
    gns3lab_prepare_view_settings($data);
    gns3lab_prepare_checks_settings($data);
    $DB->update_record('gns3lab', $data);
    // The grade type or the maximum may have changed: the item follows and the grades are computed again.
    $instance = $DB->get_record('gns3lab', ['id' => $data->id], '*', MUST_EXIST);
    $instance->cmidnumber = $data->cmidnumber ?? null;
    gns3lab_update_grades($instance);
    return true;
}

/**
 * Deletes an activity instance.
 *
 * @param int $id instance id
 * @return bool true on success, false if the instance does not exist
 */
function gns3lab_delete_instance($id): bool {
    global $DB;

    if (!$DB->record_exists('gns3lab', ['id' => $id])) {
        return false;
    }
    \mod_gns3lab\local\reveal::delete((int) $id);
    \mod_gns3lab\local\seat::delete((int) $id);
    \mod_gns3lab\local\check_runs::delete((int) $id);
    gns3lab_grade_item_delete($DB->get_record('gns3lab', ['id' => $id]));
    $DB->delete_records('gns3lab', ['id' => $id]);
    return true;
}

/**
 * Normalises what the scheme shows a student and the seat mode before the activity is saved.
 *
 * The form sends the starting nodes of a fog as a list (a multiple choice) or as text; the table keeps a JSON list.
 * Starting nodes make sense only for a fog.
 *
 * @param stdClass $data data from the module form
 */
function gns3lab_prepare_view_settings(stdClass $data): void {
    $mode = \mod_gns3lab\local\reveal::mode((object) ['factsview' => $data->factsview ?? '']);
    $data->factsview = $mode;
    $names = $data->fogstart ?? '';
    $names = is_array($names) ? $names : \mod_gns3lab\local\reveal::parse_names((string) $names);
    $data->fogstart = $mode === \mod_gns3lab\local\reveal::FOG ? \mod_gns3lab\local\reveal::encode_names($names) : '';
    // Who may drive the lab of a student at the same time (docs/seat.md); an unknown value becomes the default.
    $data->seatmode = \mod_gns3lab\local\seat::mode((object) ['seatmode' => $data->seatmode ?? '']);
}

/**
 * Normalises the auto-check settings of the activity form before the activity is saved (docs/autochecks.md).
 *
 * The form has these fields only when the lab manager offers checks; without them the stored settings stay as they are.
 * The thresholds come as rows of fields, the overrides as "code=class" lines; the table keeps both as JSON.
 *
 * @param stdClass $data data from the module form
 */
function gns3lab_prepare_checks_settings(stdClass $data): void {
    if (!property_exists($data, 'checksenabled')) {
        return;
    }
    $data->checksenabled = empty($data->checksenabled) ? 0 : 1;
    $data->checksprofile = \mod_gns3lab\local\checks::profile($data);
    $data->checksfailmark = (int) ($data->checksfailmark ?? \mod_gns3lab\local\checks::DEFAULT_FAIL_MARK);
    $rows = [];
    for ($i = 0; $i < \mod_gns3lab\local\checks::THRESHOLD_ROWS; $i++) {
        $rows[] = [
            'mark' => $data->{"checksthr{$i}mark"} ?? '',
            'critical' => $data->{"checksthr{$i}critical"} ?? '',
            'major' => $data->{"checksthr{$i}major"} ?? '',
            'minor' => $data->{"checksthr{$i}minor"} ?? '',
        ];
    }
    $rows = \mod_gns3lab\local\checks::clean_thresholds($rows);
    $data->checksthresholds = $rows && \mod_gns3lab\local\checks::thresholds_error($rows, $data->checksfailmark) === ''
        ? json_encode($rows) : '';
    [$overrides] = \mod_gns3lab\local\checks::parse_overrides((string) ($data->checksoverridestext ?? ''));
    $data->checksoverrides = $overrides ? json_encode($overrides) : '';
    $data->checksattempts = max(0, (int) ($data->checksattempts ?? 0));
    $data->checksreport = \mod_gns3lab\local\checks::report_mode($data);
    $data->checksreportdate = $data->checksreport === \mod_gns3lab\local\checks::REPORT_AFTER
        ? (int) ($data->checksreportdate ?? 0) : 0;
}

/**
 * Creates or updates the grade item of an activity (docs/autochecks.md: the grade comes from the auto-checks).
 *
 * @param stdClass $gns3lab gns3lab record, with cmidnumber when the form sent it
 * @param mixed $grades grade objects, 'reset' to reset the grades, null to update the item only
 * @return int GRADE_UPDATE_OK or another GRADE_UPDATE_* code
 */
function gns3lab_grade_item_update(stdClass $gns3lab, $grades = null): int {
    global $CFG;
    require_once($CFG->libdir . '/gradelib.php');

    $params = ['itemname' => $gns3lab->name];
    if (isset($gns3lab->cmidnumber)) {
        $params['idnumber'] = $gns3lab->cmidnumber;
    }
    $grade = (int) ($gns3lab->grade ?? 0);
    if ($grade > 0) {
        $params['gradetype'] = GRADE_TYPE_VALUE;
        $params['grademax'] = $grade;
        $params['grademin'] = 0;
    } else if ($grade < 0) {
        $params['gradetype'] = GRADE_TYPE_SCALE;
        $params['scaleid'] = -$grade;
    } else {
        $params['gradetype'] = GRADE_TYPE_NONE;
    }
    if ($grades === 'reset') {
        $params['reset'] = true;
        $grades = null;
    }
    return grade_update('mod/gns3lab', $gns3lab->course, 'mod', 'gns3lab', $gns3lab->id, 0, $grades, $params);
}

/**
 * Deletes the grade item of an activity.
 *
 * @param stdClass|false $gns3lab gns3lab record
 * @return int GRADE_UPDATE_* code
 */
function gns3lab_grade_item_delete($gns3lab): int {
    global $CFG;
    require_once($CFG->libdir . '/gradelib.php');

    if (!$gns3lab) {
        return GRADE_UPDATE_FAILED;
    }
    return grade_update('mod/gns3lab', $gns3lab->course, 'mod', 'gns3lab', $gns3lab->id, 0, null, ['deleted' => 1]);
}

/**
 * Grades of the users of an activity: the last graded auto-check run of each (docs/autochecks.md).
 *
 * @param stdClass $gns3lab gns3lab record
 * @param int $userid one user, 0 for all
 * @return array userid => grade object
 */
function gns3lab_get_user_grades(stdClass $gns3lab, int $userid = 0): array {
    return \mod_gns3lab\local\check_runs::user_grades($gns3lab, $userid);
}

/**
 * Writes the grades of an activity to the gradebook.
 *
 * @param stdClass $gns3lab gns3lab record
 * @param int $userid one user, 0 for all
 * @param bool $nullifnone a user without a graded run gets an empty grade
 */
function gns3lab_update_grades(stdClass $gns3lab, int $userid = 0, bool $nullifnone = true): void {
    if ((int) ($gns3lab->grade ?? 0) === 0) {
        gns3lab_grade_item_update($gns3lab);
    } else if ($grades = gns3lab_get_user_grades($gns3lab, $userid)) {
        gns3lab_grade_item_update($gns3lab, $grades);
    } else if ($userid && $nullifnone) {
        gns3lab_grade_item_update($gns3lab, (object) ['userid' => $userid, 'rawgrade' => null]);
    } else {
        gns3lab_grade_item_update($gns3lab);
    }
}

/**
 * Whether an activity uses a scale.
 *
 * @param int $gns3labid gns3lab id
 * @param int $scaleid scale id
 * @return bool
 */
function gns3lab_scale_used($gns3labid, $scaleid): bool {
    global $DB;

    return $scaleid && $DB->record_exists('gns3lab', ['id' => $gns3labid, 'grade' => -$scaleid]);
}

/**
 * Whether any activity uses a scale.
 *
 * @param int $scaleid scale id
 * @return bool
 */
function gns3lab_scale_used_anywhere($scaleid): bool {
    global $DB;

    return $scaleid && $DB->record_exists('gns3lab', ['grade' => -$scaleid]);
}

/**
 * Adds "Course labs" to the course navigation of a user who can watch the labs of the course.
 *
 * In Moodle 5.1 the item lands in the "More" menu of the secondary navigation. The list comes from the course
 * modinfo, there is no request to the lab manager.
 *
 * @param navigation_node $navigation the course navigation node
 * @param stdClass $course course record
 * @param context_course $context course context
 */
function gns3lab_extend_navigation_course(navigation_node $navigation, stdClass $course, context_course $context): void {
    if (!\mod_gns3lab\local\course_labs::has_observable($course)) {
        return;
    }
    $navigation->add(
        get_string('courselabs', 'gns3lab'),
        new moodle_url('/mod/gns3lab/course.php', ['id' => $course->id]),
        navigation_node::TYPE_CUSTOM,
        null,
        'gns3labcourse',
        new pix_icon('i/report', '')
    );
}
