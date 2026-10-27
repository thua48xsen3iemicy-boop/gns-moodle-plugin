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
use mod_gns3lab\local\seat;
use mod_gns3lab\local\session_data;

/**
 * Stops the current user's lab in an activity.
 *
 * Only the owner stops a lab for now: whether a teacher may stop a student's lab is an open question (SPEC §8).
 *
 * @package    mod_gns3lab
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
class stop_session extends external_api {
    /**
     * Parameters.
     *
     * @return external_function_parameters
     */
    public static function execute_parameters(): external_function_parameters {
        return new external_function_parameters([
            'cmid' => new external_value(PARAM_INT, 'Course module id'),
            'tabid' => new external_value(PARAM_ALPHANUMEXT, 'Id of this page load (one-seat rule)', VALUE_DEFAULT, ''),
        ]);
    }

    /**
     * Stops the lab.
     *
     * @param int $cmid course module id
     * @param string $tabid page id for the one-seat rule
     * @return array session
     */
    public static function execute(int $cmid, string $tabid = ''): array {
        ['cmid' => $cmid, 'tabid' => $tabid] = self::validate_parameters(
            self::execute_parameters(),
            ['cmid' => $cmid, 'tabid' => $tabid]
        );
        $access = lab_access::from_cmid($cmid);
        self::validate_context($access->context);
        [$userid] = $access->resolve_user(0);
        // A second login must not be able to stop the student's lab (docs/seat.md).
        seat::require_seat($access, $userid, $tabid);

        $client = \core\di::get(manager_client::class);
        // The session is found by user and activity, never by an id from the browser.
        $session = $client->find_session($userid, (int) $access->instance->id);
        if ($session === null) {
            throw new \moodle_exception('nosession', 'gns3lab');
        }
        if (!in_array($session->status, session_data::FINAL_STATUSES, true)) {
            $session = $client->stop_session($session->session_id);
        }
        return session_data::export($session);
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
