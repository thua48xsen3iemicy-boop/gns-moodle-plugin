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

/**
 * One-time ticket to open a node console through the console gateway (SPEC §4).
 *
 * The browser connects to the gateway directly with the ticket; Moodle never sees the WebSocket.
 * A ticket for another user's lab is always read only, whatever the browser asks.
 *
 * A node with a graphical console (console_type vnc) has no read-only mode: its screen belongs to the owner of the
 * lab. An observer gets no ticket for it, and Moodle does not even ask the manager (which would refuse too:
 * 403 console_disabled).
 *
 * @package    mod_gns3lab
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
class get_console_ticket extends external_api {
    /**
     * Parameters.
     *
     * @return external_function_parameters
     */
    public static function execute_parameters(): external_function_parameters {
        return new external_function_parameters([
            'cmid' => new external_value(PARAM_INT, 'Course module id'),
            'nodeid' => new external_value(PARAM_ALPHANUMEXT, 'Node id in the live project'),
            'userid' => new external_value(PARAM_INT, 'Whose lab, 0 for the current user', VALUE_DEFAULT, 0),
        ]);
    }

    /**
     * Issues the ticket.
     *
     * @param int $cmid course module id
     * @param string $nodeid node id
     * @param int $userid whose lab, 0 for the current user
     * @return array
     */
    public static function execute(int $cmid, string $nodeid, int $userid = 0): array {
        global $USER;

        ['cmid' => $cmid, 'nodeid' => $nodeid, 'userid' => $userid] = self::validate_parameters(
            self::execute_parameters(),
            ['cmid' => $cmid, 'nodeid' => $nodeid, 'userid' => $userid]
        );
        $access = lab_access::from_cmid($cmid);
        self::validate_context($access->context);
        [$userid, $readonly] = $access->resolve_user($userid);

        $client = \core\di::get(manager_client::class);
        $session = $client->find_session($userid, (int) $access->instance->id);
        if ($session === null || $session->status !== 'running') {
            throw new \moodle_exception('sessionnotrunning', 'gns3lab');
        }
        // The manager checks the node too, but the plugin does it first.
        if (!session_data::has_node($session, $nodeid)) {
            throw new \moodle_exception('nodenotinsession', 'gns3lab');
        }
        $type = session_data::console_type($session, $nodeid);
        if ($type === 'vnc') {
            if ($readonly) {
                throw new \moodle_exception('consolevncobserver', 'gns3lab');
            }
        } else if ($type !== '' && $type !== 'telnet') {
            throw new \moodle_exception('consolenotsupported', 'gns3lab');
        }

        $ticket = $client->issue_console_ticket($session->session_id, $nodeid, $readonly, (int) $USER->id);
        $gatewayurl = self::gateway_url($ticket->gateway_url ?? null);
        return [
            'ticket' => $ticket->ticket,
            'gatewayurl' => $gatewayurl,
            'expiresin' => (int) ($ticket->expires_in ?? 30),
            'readonly' => $readonly,
        ];
    }

    /**
     * Gateway URL for the browser: the one the manager returned, otherwise the site setting.
     *
     * @param mixed $frommanager gateway_url from the ticket answer
     * @return string URL without the trailing slash
     */
    protected static function gateway_url(mixed $frommanager): string {
        $url = is_string($frommanager) && $frommanager !== '' ? $frommanager : (string) get_config('gns3lab', 'gatewayurl');
        $url = rtrim(trim($url), '/');
        if (!preg_match('~^wss?://[^\s/?#]+(/[^\s?#]*)?$~i', $url)) {
            throw new \moodle_exception('gatewaynotconfigured', 'gns3lab');
        }
        return $url;
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
            'readonly' => new external_value(PARAM_BOOL, 'Input is dropped by the gateway'),
        ]);
    }
}
