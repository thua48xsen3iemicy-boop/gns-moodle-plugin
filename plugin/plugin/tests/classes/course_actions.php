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

namespace mod_gns3lab\tests;

use core_courseformat\formatactions;
use core_courseformat\local\cmactions;

/**
 * Course module actions for tests on every supported Moodle version.
 *
 * Moodle 5.2 deprecated course_delete_module() and set_coursemodule_groupmode() in favour of the course format
 * cm actions (MDL-86856, MDL-86857); Moodle 5.1 has no such actions yet.
 *
 * @package    mod_gns3lab
 * @category   test
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
class course_actions {
    /**
     * Deletes a course module with all its data.
     *
     * @param int $cmid course module id
     */
    public static function delete_module(int $cmid): void {
        global $CFG;

        if (method_exists(cmactions::class, 'delete')) {
            $courseid = get_coursemodule_from_id('', $cmid, 0, false, MUST_EXIST)->course;
            formatactions::cm($courseid)->delete($cmid);
            return;
        }
        require_once($CFG->dirroot . '/course/lib.php');
        course_delete_module($cmid);
    }

    /**
     * Sets the group mode of a course module.
     *
     * @param int $cmid course module id
     * @param int $groupmode NOGROUPS, SEPARATEGROUPS or VISIBLEGROUPS
     */
    public static function set_groupmode(int $cmid, int $groupmode): void {
        global $CFG;

        if (method_exists(cmactions::class, 'set_groupmode')) {
            $courseid = get_coursemodule_from_id('', $cmid, 0, false, MUST_EXIST)->course;
            formatactions::cm($courseid)->set_groupmode($cmid, $groupmode);
            return;
        }
        require_once($CFG->dirroot . '/course/lib.php');
        set_coursemodule_groupmode($cmid, $groupmode);
    }
}
