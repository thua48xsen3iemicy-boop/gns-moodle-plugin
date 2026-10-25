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
use mod_gns3lab\local\session_data;
use mod_gns3lab\local\session_facts;

/**
 * Asks the lab manager to collect the facts of a running lab now ("Refresh now" of the details card).
 *
 * The manager decides how often a collection really happens (CONTRACTS §2.7), so the plugin has no limit of its own.
 * Observers may ask too: collecting facts changes nothing in the lab.
 *
 * @package    mod_gns3lab
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
class refresh_facts extends external_api {
    /**
     * Parameters.
     *
     * @return external_function_parameters
     */
    public static function execute_parameters(): external_function_parameters {
        return new external_function_parameters([
            'cmid' => new external_value(PARAM_INT, 'Course module id'),
            'userid' => new external_value(PARAM_INT, 'Whose lab, 0 for the current user', VALUE_DEFAULT, 0),
        ]);
    }

    /**
     * Requests the collection.
     *
     * @param int $cmid course module id
     * @param int $userid whose lab, 0 for the current user
     * @return array
     */
    public static function execute(int $cmid, int $userid = 0): array {
        ['cmid' => $cmid, 'userid' => $userid] = self::validate_parameters(
            self::execute_parameters(),
            ['cmid' => $cmid, 'userid' => $userid]
        );
        $access = lab_access::from_cmid($cmid);
        self::validate_context($access->context);
        require_capability('mod/gns3lab:view', $access->context);
        [$userid] = $access->resolve_user($userid);

        $client = \core\di::get(manager_client::class);
        $session = $client->find_session($userid, (int) $access->instance->id);
        if ($session === null || $session->status !== 'running') {
            throw new \moodle_exception('sessionnotrunning', 'gns3lab');
        }
        if (!session_data::has_feature($session, 'facts')) {
            throw new \moodle_exception('factsnotavailable', 'gns3lab');
        }
        $client->refresh_session_facts($session->session_id);
        // The next read asks the manager instead of serving the snapshot from before the collection.
        session_facts::forget($session->session_id);
        return ['requested' => true];
    }

    /**
     * Result.
     *
     * @return external_single_structure
     */
    public static function execute_returns(): external_single_structure {
        return new external_single_structure([
            'requested' => new external_value(PARAM_BOOL, 'The manager took the request'),
        ]);
    }
}
