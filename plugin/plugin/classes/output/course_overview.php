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
 * The course page of a teacher: the frame and the groups to pick from. The table is drawn by amd/src/course.js.
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
     */
    public function __construct(
        /** @var \stdClass course record */
        protected \stdClass $course
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
        return [
            'courseid' => (int) $this->course->id,
            'coursename' => format_string($this->course->fullname, true, ['context' => $context]),
            'courseurl' => (new url('/course/view.php', ['id' => $this->course->id]))->out(false),
            'labbase' => (new url('/mod/gns3lab/lab.php'))->out(false),
            'groups' => $groups,
            'hasgroups' => $groups !== [],
        ];
    }
}
