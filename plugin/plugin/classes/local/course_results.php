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

namespace mod_gns3lab\local;

use core_external\external_single_structure;
use core_external\external_value;

/**
 * What the course page shows about the results of the students: the grades of the gradebook and a summary of the checks.
 *
 * The gradebook is the only source of the grades: the page reads the final grade, so a grade the teacher changed by
 * hand in the gradebook is the one shown (marked as overridden). Nothing is written here.
 *
 * @package    mod_gns3lab
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
class course_results {
    /**
     * Whether the current user may see the grades of the students of the course.
     *
     * @param \context_course $context course context
     * @return bool
     */
    public static function can_see_grades(\context_course $context): bool {
        return has_capability('moodle/grade:viewall', $context);
    }

    /**
     * The grade item of a lab and the grades of some of its students.
     *
     * @param \stdClass $course course record
     * @param \cm_info $cm the lab
     * @param int $grade the grade setting of the activity: 0 none, above 0 the top points, below 0 a scale
     * @param int[] $userids students
     * @return array ['lab' => fields of the lab (lab_fields()), 'grades' => userid => fields of a grade (grade_fields())]
     */
    public static function grades(\stdClass $course, \cm_info $cm, int $grade, array $userids): array {
        global $CFG;
        require_once($CFG->libdir . '/gradelib.php');

        $lab = ['gradetype' => 'none', 'grademax' => '', 'gradepass' => '', 'average' => ''];
        if ($grade === 0) {
            return ['lab' => $lab, 'grades' => []];
        }
        $item = \grade_item::fetch([
            'courseid' => $course->id, 'itemtype' => 'mod', 'itemmodule' => 'gns3lab',
            'iteminstance' => $cm->instance, 'itemnumber' => 0,
        ]);
        if (!$item || !in_array((int) $item->gradetype, [GRADE_TYPE_VALUE, GRADE_TYPE_SCALE], true)) {
            return ['lab' => $lab, 'grades' => []];
        }
        $scale = (int) $item->gradetype === GRADE_TYPE_SCALE;
        $pass = (float) $item->gradepass;
        $lab['gradetype'] = $scale ? 'scale' : 'value';
        $lab['grademax'] = $scale ? '' : grade_format_gradevalue($item->grademax, $item, true, GRADE_DISPLAY_TYPE_REAL);
        $lab['gradepass'] = $pass > 0 ? grade_format_gradevalue($pass, $item, true) : '';

        $context = \context_course::instance($course->id);
        $hidden = has_capability('moodle/grade:viewhidden', $context);
        if (!$userids || ($item->is_hidden() && !$hidden)) {
            return ['lab' => $lab, 'grades' => []];
        }
        $out = [];
        $sum = 0.0;
        foreach (\grade_grade::fetch_users_grades($item, $userids, false) as $userid => $row) {
            if ($row->finalgrade === null || ($row->is_hidden() && !$hidden)) {
                continue;
            }
            $value = (float) $row->finalgrade;
            $sum += $value;
            $range = (float) $item->grademax - (float) $item->grademin;
            $out[(int) $userid] = [
                'text' => grade_format_gradevalue($value, $item, true),
                'ratio' => $range > 0 ? round(max(0, min(1, ($value - (float) $item->grademin) / $range)), 4) : 0.0,
                'passed' => $pass > 0 ? ($value >= $pass ? 'yes' : 'no') : '',
                'overridden' => !empty($row->overridden),
                'gradedat' => (int) ($row->timemodified ?: 0),
            ];
        }
        if ($out && !$scale) {
            $lab['average'] = grade_format_gradevalue($sum / count($out), $item, true);
        }
        return ['lab' => $lab, 'grades' => $out];
    }

    /**
     * A summary of the auto-check runs of some students of a lab: graded attempts and the last run.
     *
     * @param int $instanceid gns3lab id
     * @param int[] $userids students
     * @return array userid => fields of a check summary (check_fields())
     */
    public static function checks(int $instanceid, array $userids): array {
        global $DB;

        if (!$userids) {
            return [];
        }
        [$insql, $params] = $DB->get_in_or_equal($userids, SQL_PARAMS_NAMED);
        $rows = $DB->get_records_select(
            check_runs::TABLE,
            "gns3labid = :labid AND userid {$insql}",
            $params + ['labid' => $instanceid],
            'id ASC',
            'id, userid, status, outcome, mark, timecreated, timefinished'
        );
        $out = [];
        foreach ($rows as $row) {
            $userid = (int) $row->userid;
            $entry = $out[$userid] ?? ['attempts' => 0];
            if (check_runs::is_graded($row)) {
                $entry['attempts']++;
            }
            // Ordered by id: the last row of the user is the last run.
            $entry['laststatus'] = (string) $row->status;
            $entry['lastoutcome'] = (string) $row->outcome;
            $entry['lastat'] = (int) ($row->timefinished ?: $row->timecreated);
            $out[$userid] = $entry;
        }
        return $out;
    }

    /**
     * Description of the grade fields of a lab for execute_returns().
     *
     * @return array fields of external_single_structure
     */
    public static function lab_fields(): array {
        return [
            'gradetype' => new external_value(PARAM_ALPHA, 'none, value or scale; none also when the user may not see grades'),
            'grademax' => new external_value(PARAM_TEXT, 'Top points of a value grade, formatted; empty for a scale'),
            'gradepass' => new external_value(PARAM_TEXT, 'Grade to pass, formatted; empty if the gradebook sets none'),
            'average' => new external_value(PARAM_TEXT, 'Average grade of the graded students in the answer; empty for a scale'),
        ];
    }

    /**
     * Description of a grade of a cell for execute_returns().
     *
     * @return external_single_structure
     */
    public static function grade_structure(): external_single_structure {
        return new external_single_structure([
            'text' => new external_value(PARAM_TEXT, 'Final grade of the gradebook, formatted as the gradebook shows it'),
            'ratio' => new external_value(PARAM_FLOAT, 'Where the grade is between the lowest and the top, 0 to 1'),
            'passed' => new external_value(PARAM_ALPHA, 'yes or no against the grade to pass; empty if there is none'),
            'overridden' => new external_value(PARAM_BOOL, 'The grade was changed by hand in the gradebook'),
            'gradedat' => new external_value(PARAM_INT, 'When the grade was last changed'),
        ], 'Final grade of the gradebook, null if there is none or the user may not see it', VALUE_DEFAULT, null, NULL_ALLOWED);
    }

    /**
     * Description of the check summary of a cell for execute_returns().
     *
     * @return external_single_structure
     */
    public static function checks_structure(): external_single_structure {
        return new external_single_structure([
            'attempts' => new external_value(PARAM_INT, 'Graded attempts'),
            'laststatus' => new external_value(PARAM_ALPHA, 'Status of the last run: queued, running, done or failed'),
            'lastoutcome' => new external_value(PARAM_ALPHA, 'Outcome of the last run: graded or incomplete when done'),
            'lastat' => new external_value(PARAM_INT, 'When the last run ended, or was asked for while it goes on'),
        ], 'Auto-check runs of the student, null if there are none', VALUE_DEFAULT, null, NULL_ALLOWED);
    }
}
