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
use mod_gns3lab\local\check_runs;
use mod_gns3lab\local\checks;
use mod_gns3lab\local\lab_access;
use mod_gns3lab\local\manager_client;
use mod_gns3lab\local\manager_exception;
use mod_gns3lab\local\seat;
use mod_gns3lab\local\session_data;

/**
 * Starts an auto-check of the running lab of the current user (docs/autochecks.md).
 *
 * Only the owner of the lab, from the page that holds the seat (docs/seat.md): a check spends an attempt and may change
 * the grade, so it is an act of the student on the lab like starting it. The grading comes from the activity, never
 * from the browser. A run that is already going on is returned instead of a new one; with no attempts left the check
 * is refused before the manager is asked.
 *
 * @package    mod_gns3lab
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
class start_check extends external_api {
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
     * Starts the check.
     *
     * @param int $cmid course module id
     * @param string $tabid page id for the one-seat rule
     * @return array as get_checks, with the new run first
     */
    public static function execute(int $cmid, string $tabid = ''): array {
        global $USER;

        ['cmid' => $cmid, 'tabid' => $tabid] = self::validate_parameters(
            self::execute_parameters(),
            ['cmid' => $cmid, 'tabid' => $tabid]
        );
        $access = lab_access::from_cmid($cmid);
        self::validate_context($access->context);
        [$userid] = $access->resolve_user(0);
        $instance = $access->instance;
        if (!checks::enabled($instance)) {
            throw new \moodle_exception('checksdisabled', 'gns3lab');
        }
        $client = \core\di::get(manager_client::class);
        if (!$client->has_feature('checks')) {
            throw new \moodle_exception('checksnotavailable', 'gns3lab');
        }
        seat::require_seat($access, $userid, $tabid);

        $session = $client->find_session($userid, (int) $instance->id);
        if ($session === null || $session->status !== 'running') {
            throw new \moodle_exception('sessionnotrunning', 'gns3lab');
        }
        if (!session_data::has_feature($session, 'checks')) {
            throw new \moodle_exception('checksnotavailable', 'gns3lab');
        }
        check_runs::sync($instance, $userid, $client);
        if (check_runs::pending((int) $instance->id, $userid, $session->session_id)) {
            return get_checks::overview($access, $userid, false, false);
        }
        if (checks::attempts_left($instance, check_runs::attempts_used((int) $instance->id, $userid)) === 0) {
            throw new \moodle_exception('checksnoattempts', 'gns3lab');
        }
        try {
            $run = $client->start_check($session->session_id, (int) $USER->id, checks::grading($instance));
        } catch (manager_exception $e) {
            $runid = $e->details->run_id ?? null;
            if ($e->remotecode === 'check_in_progress' && is_string($runid)) {
                // A run this page did not record (an earlier answer was lost): follow it instead of a new one.
                $run = $client->get_check($session->session_id, $runid);
            } else if ($e->remotecode === 'session_not_running') {
                throw new \moodle_exception('sessionnotrunning', 'gns3lab');
            } else {
                throw $e;
            }
        }
        check_runs::record($instance, $userid, $session->session_id, $run, (int) $USER->id);
        return get_checks::overview($access, $userid, false, false);
    }

    /**
     * Result.
     *
     * @return external_single_structure
     */
    public static function execute_returns(): external_single_structure {
        return get_checks::execute_returns();
    }
}
