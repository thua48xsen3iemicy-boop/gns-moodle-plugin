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

/**
 * Which labs of a course a teacher may watch, which groups the teacher may pick and who the students are.
 *
 * Shared by the course page, the course navigation and mod_gns3lab_get_course_overview, so that all of them
 * agree on who sees what. Nothing here talks to the lab manager.
 *
 * @package    mod_gns3lab
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
class course_labs {
    /**
     * Lab activities of a course in the order of the course that the user sees and may observe.
     *
     * @param \stdClass $course course record
     * @param int $userid the teacher, 0 for the current user
     * @return \cm_info[] by course module id
     */
    public static function observable(\stdClass $course, int $userid = 0): array {
        global $USER;

        $userid = $userid ?: (int) $USER->id;
        $modinfo = get_fast_modinfo($course, $userid);
        $result = [];
        foreach ($modinfo->get_section_info_all() as $section) {
            foreach ($modinfo->sections[$section->section] ?? [] as $cmid) {
                $cm = $modinfo->get_cm($cmid);
                if ($cm->modname !== 'gns3lab' || !$cm->uservisible || $cm->deletioninprogress) {
                    continue;
                }
                if (has_capability('mod/gns3lab:observe', $cm->context, $userid)) {
                    $result[$cm->id] = $cm;
                }
            }
        }
        return $result;
    }

    /**
     * Whether the user can watch at least one lab of the course: the navigation item and the links depend on it.
     *
     * @param \stdClass $course course record
     * @return bool
     */
    public static function has_observable(\stdClass $course): bool {
        return self::observable($course) !== [];
    }

    /**
     * Groups of the course the current user may pick on the page: all of them with moodle/site:accessallgroups,
     * otherwise the user's own.
     *
     * @param \stdClass $course course record
     * @return \stdClass[] group records (id, name) by id, ordered by name
     */
    public static function groups(\stdClass $course): array {
        global $USER;

        $context = \context_course::instance($course->id);
        $userid = has_capability('moodle/site:accessallgroups', $context) ? 0 : (int) $USER->id;
        return groups_get_all_groups($course->id, $userid, 0, 'g.id, g.name', false, true);
    }

    /**
     * Students of a lab: users who can use it and cannot observe it, optionally members of one group.
     *
     * Teachers who can also use the activity are not students.
     *
     * @param \cm_info $cm the lab
     * @param int $groupid group to take, 0 for all
     * @return \stdClass[] user records with the name fields and the picture fields, by id
     */
    public static function students(\cm_info $cm, int $groupid = 0): array {
        $fields = 'u.' . implode(', u.', \core_user\fields::for_userpic()->get_required_fields());
        $users = get_enrolled_users($cm->context, 'mod/gns3lab:use', $groupid, $fields, 'u.lastname, u.firstname', 0, 0, true);
        $observers = get_enrolled_users($cm->context, 'mod/gns3lab:observe', 0, 'u.id', null, 0, 0, true);
        return array_diff_key($users, $observers);
    }
}
