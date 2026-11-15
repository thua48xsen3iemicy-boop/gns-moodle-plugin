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

namespace mod_gns3lab\output;

use core\output\renderable;
use core\output\renderer_base;
use core\output\templatable;
use core\url;
use mod_gns3lab\local\course_labs;

/**
 * The course page of a teacher: the frame, the groups to pick from and the link to the gradebook. The views (the summary and
 * one lab) and the table are drawn by amd/src/course.js.
 *
 * @package    mod_gns3lab
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
class course_overview implements renderable, templatable {
    /**
     * Constructor.
     *
     * @param \stdClass $course course record
     * @param int $labid course module id of the lab to show first, 0 for the summary
     */
    public function __construct(
        /** @var \stdClass course record */
        protected \stdClass $course,
        /** @var int course module id of the lab to show first, 0 for the summary */
        protected int $labid = 0
    ) {
    }

    /**
     * Exports the template context.
     *
     * @param renderer_base $output renderer
     * @return array
     */
    public function export_for_template(renderer_base $output): array {
        $context = \context_course::instance($this->course->id);
        $groups = [];
        foreach (course_labs::groups($this->course) as $group) {
            $groups[] = ['id' => (int) $group->id, 'name' => format_string($group->name, true, ['context' => $context])];
        }
        // The views: the summary first, then the labs in the order of the course. A lab not on the list falls back to the summary.
        $labs = [];
        foreach (course_labs::observable($this->course) as $cm) {
            $labs[] = [
                'cmid' => (int) $cm->id,
                'name' => format_string($cm->name, true, ['context' => $cm->context]),
                'url' => (new url('/mod/gns3lab/course.php', ['id' => $this->course->id, 'lab' => $cm->id]))->out(false),
                'current' => (int) $cm->id === $this->labid,
            ];
        }
        $labid = in_array($this->labid, array_column($labs, 'cmid'), true) ? $this->labid : 0;
        // The grader report is where grades are changed; the page only shows them.
        $gradebook = has_capability('moodle/grade:viewall', $context) && has_capability('gradereport/grader:view', $context)
            ? (new url('/grade/report/grader/index.php', ['id' => $this->course->id]))->out(false) : '';
        return [
            'courseid' => (int) $this->course->id,
            'labid' => $labid,
            'summaryurl' => (new url('/mod/gns3lab/course.php', ['id' => $this->course->id]))->out(false),
            'labs' => $labs,
            'gradebookurl' => $gradebook,
            'coursename' => format_string($this->course->fullname, true, ['context' => $context]),
            'courseurl' => (new url('/course/view.php', ['id' => $this->course->id]))->out(false),
            'labbase' => (new url('/mod/gns3lab/lab.php'))->out(false),
            'groups' => $groups,
            'hasgroups' => $groups !== [],
        ];
    }
}
