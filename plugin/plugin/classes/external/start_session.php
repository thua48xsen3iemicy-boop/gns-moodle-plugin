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
use core_external\external_single_structure;
use core_external\external_value;
use mod_gns3lab\local\lab_access;
use mod_gns3lab\local\manager_client;
use mod_gns3lab\local\manager_exception;
use mod_gns3lab\local\session_data;

/**
 * Starts the current user's lab, or returns the live session of this activity (the manager is idempotent).
 *
 * @package    mod_gns3lab
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
class start_session extends external_api {
    /**
     * Parameters.
     *
     * @return external_function_parameters
     */
    public static function execute_parameters(): external_function_parameters {
        return new external_function_parameters([
            'cmid' => new external_value(PARAM_INT, 'Course module id'),
        ]);
    }

    /**
     * Starts the lab.
     *
     * @param int $cmid course module id
     * @return array session
     */
    public static function execute(int $cmid): array {
        ['cmid' => $cmid] = self::validate_parameters(self::execute_parameters(), ['cmid' => $cmid]);
        $access = lab_access::from_cmid($cmid);
        self::validate_context($access->context);
        [$userid] = $access->resolve_user(0);

        $labref = trim($access->instance->labref);
        if ($labref === '') {
            throw new \moodle_exception('nolabref', 'gns3lab');
        }
        try {
            $session = \core\di::get(manager_client::class)->create_session(
                $userid,
                (int) $access->instance->id,
                (int) $access->course->id,
                $labref,
                (int) $access->instance->maxduration
            );
        } catch (manager_exception $e) {
            if ($e->remotecode === 'session_conflict') {
                self::conflict($e);
            }
            throw $e;
        }
        return session_data::export($session);
    }

    /**
     * Turns a conflict into a message that names the other activity, if the user can see it.
     *
     * The manager reports the student's own other live lab (CONTRACTS §2.2). The link travels as the
     * exception link, the page shows it next to the message.
     *
     * @param manager_exception $e session_conflict from the manager
     */
    protected static function conflict(manager_exception $e): void {
        $instanceid = (int) ($e->details->conflict->instance_id ?? 0);
        if ($instanceid <= 0) {
            return;
        }
        $cm = get_coursemodule_from_instance('gns3lab', $instanceid);
        if (!$cm) {
            return;
        }
        $modinfo = get_fast_modinfo($cm->course);
        $cminfo = $modinfo->get_cm($cm->id);
        if (!can_access_course($modinfo->get_course()) || !$cminfo->uservisible) {
            return;
        }
        $url = new \core\url('/mod/gns3lab/view.php', ['id' => $cm->id]);
        throw new \moodle_exception('sessionconflictat', 'gns3lab', $url->out(false), $cminfo->get_formatted_name());
    }

    /**
     * Result.
     *
     * @return external_single_structure
     */
    public static function execute_returns(): external_single_structure {
        return session_data::structure();
    }
}
