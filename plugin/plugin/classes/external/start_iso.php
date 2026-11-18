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
use mod_gns3lab\local\seat;
use mod_gns3lab\local\session_data;

/**
 * "Mount ISO": the files of the agreed directory of a source node become an ISO image in the CD-ROM drive of the
 * other target nodes of the lab (manager CONTRACTS §2.12).
 *
 * The owner's action on the owner's running lab: it changes the lab, so it takes the seat (docs/seat.md). Observers
 * cannot ask. Which nodes are sources and targets is the image catalog's decision, the manager checks it again.
 *
 * @package    mod_gns3lab
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
class start_iso extends external_api {
    /** @var string[] manager refusals (CONTRACTS §2.12) and the language strings that explain them */
    private const REFUSALS = [
        'session_not_running' => 'sessionnotrunning',
        'node_not_found' => 'isonotavailable',
        'iso_not_available' => 'isonotavailable',
        'iso_not_supported' => 'isonotsupported',
        'iso_in_progress' => 'isoinprogress',
    ];

    /**
     * Parameters.
     *
     * @return external_function_parameters
     */
    public static function execute_parameters(): external_function_parameters {
        return new external_function_parameters([
            'cmid' => new external_value(PARAM_INT, 'Course module id'),
            'nodeid' => new external_value(PARAM_ALPHANUMEXT, 'Source node id in the live project'),
            'tabid' => new external_value(PARAM_ALPHANUMEXT, 'Id of this page load (one-seat rule)', VALUE_DEFAULT, ''),
        ]);
    }

    /**
     * Starts the operation.
     *
     * @param int $cmid course module id
     * @param string $nodeid source node id
     * @param string $tabid page of the caller
     * @return array
     */
    public static function execute(int $cmid, string $nodeid, string $tabid = ''): array {
        global $USER;

        ['cmid' => $cmid, 'nodeid' => $nodeid, 'tabid' => $tabid] = self::validate_parameters(
            self::execute_parameters(),
            ['cmid' => $cmid, 'nodeid' => $nodeid, 'tabid' => $tabid]
        );
        $access = lab_access::from_cmid($cmid);
        self::validate_context($access->context);
        [$userid] = $access->resolve_user(0);
        $client = \core\di::get(manager_client::class);
        seat::require_seat($access, $userid, $tabid);

        $session = $client->find_session($userid, (int) $access->instance->id);
        if ($session === null || $session->status !== 'running') {
            throw new \moodle_exception('sessionnotrunning', 'gns3lab');
        }
        if (!in_array($nodeid, session_data::iso_nodes($session, 'sources'), true)) {
            throw new \moodle_exception('isonotavailable', 'gns3lab');
        }
        try {
            $op = $client->start_iso($session->session_id, $nodeid, (int) $USER->id);
        } catch (manager_exception $e) {
            if (isset(self::REFUSALS[$e->remotecode])) {
                throw new \moodle_exception(self::REFUSALS[$e->remotecode], 'gns3lab');
            }
            throw $e;
        }
        return get_iso::export($op);
    }

    /**
     * Result.
     *
     * @return external_single_structure
     */
    public static function execute_returns(): external_single_structure {
        return get_iso::execute_returns();
    }
}
