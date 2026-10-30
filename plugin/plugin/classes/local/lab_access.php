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
 * Activity, context and the permission checks shared by the external functions.
 *
 * Whose lab a request is about is decided here, on the server: the browser only says which user
 * it wants to look at, and anything but the current user needs mod/gns3lab:observe and a shared group.
 *
 * @package    mod_gns3lab
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
class lab_access {
    /**
     * Constructor.
     *
     * @param \stdClass $course course record
     * @param \cm_info $cm course module
     * @param \context_module $context module context
     * @param \stdClass $instance gns3lab record
     */
    protected function __construct(
        /** @var \stdClass course record */
        public readonly \stdClass $course,
        /** @var \cm_info course module */
        public readonly \cm_info $cm,
        /** @var \context_module module context */
        public readonly \context_module $context,
        /** @var \stdClass gns3lab record */
        public readonly \stdClass $instance
    ) {
    }

    /**
     * Loads the activity by course module id. The caller still has to validate the context.
     *
     * @param int $cmid course module id
     * @return self
     */
    public static function from_cmid(int $cmid): self {
        global $DB;

        [$course, $cm] = get_course_and_cm_from_cmid($cmid, 'gns3lab');
        $instance = $DB->get_record('gns3lab', ['id' => $cm->instance], '*', MUST_EXIST);
        return new self($course, $cm, \context_module::instance($cm->id), $instance);
    }

    /**
     * Decides whose lab the request is about and whether it is read only.
     *
     * The current user (0 or own id) needs mod/gns3lab:use. Another user needs mod/gns3lab:observe,
     * a shared group in separate groups mode, and must be someone who can use the activity;
     * such access is always read only.
     *
     * @param int $userid requested user, 0 for the current user
     * @return array [int userid, bool readonly]
     */
    public function resolve_user(int $userid): array {
        global $USER;

        if ($userid === 0 || $userid === (int) $USER->id) {
            require_capability('mod/gns3lab:use', $this->context);
            return [(int) $USER->id, false];
        }
        require_capability('mod/gns3lab:observe', $this->context);
        if (!$this->can_observe_user($userid)) {
            throw new \moodle_exception('cannotobserveuser', 'gns3lab');
        }
        return [$userid, true];
    }

    /**
     * Whether the current user may observe the lab of another user.
     *
     * Checks the group and that the other user can use the activity; mod/gns3lab:observe is checked by the caller.
     *
     * @param int $userid the other user
     * @return bool
     */
    public function can_observe_user(int $userid): bool {
        if (!is_enrolled($this->context, $userid, 'mod/gns3lab:use', true)) {
            return false;
        }
        return $this->share_group([$userid]) !== [];
    }

    /**
     * Which of the given users the current user may observe as far as the groups go.
     *
     * One query for the whole list, so that a table of many students does not ask per student. The caller has made
     * sure that the users can use the activity (can_observe_user() checks it for one user).
     *
     * @param int[] $userids users who can use the activity
     * @return int[] those of them in a group of the current user, or all of them without separate groups
     */
    public function share_group(array $userids): array {
        global $USER;

        $groupmode = groups_get_activity_groupmode($this->cm, $this->course);
        if (!$userids || $groupmode != SEPARATEGROUPS || has_capability('moodle/site:accessallgroups', $this->context)) {
            return array_values($userids);
        }
        $mine = groups_get_all_groups($this->course->id, $USER->id, $this->cm->groupingid, 'g.id');
        if (!$mine) {
            return [];
        }
        $members = groups_get_groups_members(array_keys($mine), null, 'u.id ASC') ?: [];
        return array_values(array_filter($userids, fn($id) => isset($members[$id])));
    }
}
