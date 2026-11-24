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

/**
 * One-time ticket to follow the consoles of an observed student (manager CONTRACTS §6.3, docs/teacher-dashboard.md §15).
 *
 * With it the browser opens `<gateway>/console/activity` and learns which consoles the student has open and when they
 * type into one; the input itself never comes. Only an observer gets it: the student sees their own consoles, and the
 * ticket names the lab of another user (mod/gns3lab:observe and a shared group, lab_access::resolve_user()).
 *
 * @package    mod_gns3lab
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
class get_activity_ticket extends external_api {
    /** Feature of the lab manager (GET /api/v1/info). */
    public const FEATURE = 'console-activity';

    /**
     * Parameters.
     *
     * @return external_function_parameters
     */
    public static function execute_parameters(): external_function_parameters {
        return new external_function_parameters([
            'cmid' => new external_value(PARAM_INT, 'Course module id'),
            'userid' => new external_value(PARAM_INT, 'The observed student'),
        ]);
    }

    /**
     * Issues the ticket.
     *
     * @param int $cmid course module id
     * @param int $userid the observed student
     * @return array
     */
    public static function execute(int $cmid, int $userid): array {
        global $USER;

        ['cmid' => $cmid, 'userid' => $userid] = self::validate_parameters(
            self::execute_parameters(),
            ['cmid' => $cmid, 'userid' => $userid]
        );
        $access = lab_access::from_cmid($cmid);
        self::validate_context($access->context);
        [$userid, $readonly] = $access->resolve_user($userid);
        if (!$readonly) {
            throw new \moodle_exception('consoleactivityobserver', 'gns3lab');
        }

        $client = \core\di::get(manager_client::class);
        if (!$client->has_feature(self::FEATURE)) {
            throw new \moodle_exception('consoleactivityunsupported', 'gns3lab');
        }
        $session = $client->find_session($userid, (int) $access->instance->id);
        if ($session === null || $session->status !== 'running') {
            throw new \moodle_exception('sessionnotrunning', 'gns3lab');
        }

        $ticket = $client->issue_activity_ticket($session->session_id, (int) $USER->id);
        return [
            'ticket' => $ticket->ticket,
            'gatewayurl' => get_console_ticket::gateway_url($ticket->gateway_url ?? null),
            'expiresin' => (int) ($ticket->expires_in ?? 30),
        ];
    }

    /**
     * Result.
     *
     * @return external_single_structure
     */
    public static function execute_returns(): external_single_structure {
        return new external_single_structure([
            'ticket' => new external_value(PARAM_RAW, 'One-time ticket, valid for a few seconds'),
            'gatewayurl' => new external_value(PARAM_RAW, 'Gateway base URL, ws:// or wss://'),
            'expiresin' => new external_value(PARAM_INT, 'Ticket lifetime, seconds'),
        ]);
    }
}
