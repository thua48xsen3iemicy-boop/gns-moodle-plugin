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
 * Facts about the devices of a lab (docs/live-state.md): the layers L2, L3 and OSPF of the scheme are built from them.
 *
 * The session is found by the server for the user the request is allowed to see; the browser never names it.
 * The page polls while the lab runs and passes the rev it has: the same rev comes back without the document.
 *
 * @package    mod_gns3lab
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
class get_facts extends external_api {
    /**
     * Parameters.
     *
     * @return external_function_parameters
     */
    public static function execute_parameters(): external_function_parameters {
        return new external_function_parameters([
            'cmid' => new external_value(PARAM_INT, 'Course module id'),
            'userid' => new external_value(PARAM_INT, 'Whose lab, 0 for the current user', VALUE_DEFAULT, 0),
            'sincerev' => new external_value(PARAM_INT, 'Rev of the facts the page has, 0 for none', VALUE_DEFAULT, 0),
        ]);
    }

    /**
     * Returns the facts snapshot of the session.
     *
     * @param int $cmid course module id
     * @param int $userid whose lab, 0 for the current user
     * @param int $sincerev rev of the facts the page has, 0 for none
     * @return array
     */
    public static function execute(int $cmid, int $userid = 0, int $sincerev = 0): array {
        ['cmid' => $cmid, 'userid' => $userid, 'sincerev' => $sincerev] = self::validate_parameters(
            self::execute_parameters(),
            ['cmid' => $cmid, 'userid' => $userid, 'sincerev' => $sincerev]
        );
        $access = lab_access::from_cmid($cmid);
        self::validate_context($access->context);
        require_capability('mod/gns3lab:view', $access->context);
        [$userid] = $access->resolve_user($userid);

        $result = ['available' => false, 'changed' => false, 'rev' => 0, 'generatedat' => 0, 'ttl' => 0, 'facts' => ''];
        if (!manager_client::is_configured()) {
            return $result;
        }
        $client = \core\di::get(manager_client::class);
        $session = $client->find_session($userid, (int) $access->instance->id);
        // The manager reads facts only when the slot reports them (CONTRACTS §7).
        if ($session === null || !session_data::has_feature($session, 'facts')) {
            return $result;
        }
        $entry = session_facts::get($client, $session->session_id);
        $changed = $sincerev <= 0 || $sincerev !== $entry->rev || !empty($entry->l1);
        return [
            'available' => true,
            'changed' => $changed,
            'rev' => $entry->rev,
            'generatedat' => $entry->generatedat,
            'ttl' => $entry->ttl,
            'facts' => $changed ? $entry->doc : '',
        ];
    }

    /**
     * Result.
     *
     * @return external_single_structure
     */
    public static function execute_returns(): external_single_structure {
        return new external_single_structure([
            'available' => new external_value(PARAM_BOOL, 'The session reports facts'),
            'changed' => new external_value(PARAM_BOOL, 'A document is included; L1 freshness may change at the same rev'),
            'rev' => new external_value(PARAM_INT, 'Rev of the facts, 0 if there were none yet'),
            'generatedat' => new external_value(PARAM_INT, 'Time of the last collection, 0 if unknown'),
            'ttl' => new external_value(PARAM_INT, 'Seconds after which the facts count as out of date'),
            'facts' => new external_value(PARAM_RAW, 'Facts document gns3lab.facts/1 as JSON, empty unless changed'),
        ]);
    }
}
