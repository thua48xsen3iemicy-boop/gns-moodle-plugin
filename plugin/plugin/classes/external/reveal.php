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
use core_external\external_multiple_structure;
use core_external\external_single_structure;
use core_external\external_value;
use mod_gns3lab\local\lab_access;
use mod_gns3lab\local\manager_client;
use mod_gns3lab\local\reveal as reveal_store;
use mod_gns3lab\local\seat;

/**
 * Adds items to the mask of the current user: what the student's commands and the cables of the lab uncovered.
 *
 * Only the owner of a running session writes, and only nodes and links of that session's live topology are taken.
 * Items are only added; the rows of an earlier project of the lab are dropped on the way.
 *
 * @package    mod_gns3lab
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
class reveal extends external_api {
    /**
     * Parameters.
     *
     * @return external_function_parameters
     */
    public static function execute_parameters(): external_function_parameters {
        return new external_function_parameters([
            'cmid' => new external_value(PARAM_INT, 'Course module id'),
            'items' => new external_multiple_structure(new external_single_structure([
                'nodeid' => new external_value(PARAM_ALPHANUMEXT, 'Node of the live project, empty for a link', VALUE_DEFAULT, ''),
                'kind' => new external_value(PARAM_ALPHA, 'node, known, iface, group or link'),
                'item' => new external_value(
                    PARAM_RAW,
                    'Interface name, data group or link id, empty for node and known',
                    VALUE_DEFAULT,
                    ''
                ),
            ]), 'What to uncover, up to ' . reveal_store::MAX_PER_CALL . ' items'),
            'tabid' => new external_value(PARAM_ALPHANUMEXT, 'Id of this page load (one-seat rule)', VALUE_DEFAULT, ''),
        ]);
    }

    /**
     * Adds the items.
     *
     * @param int $cmid course module id
     * @param array $items each: nodeid, kind, item
     * @param string $tabid page id for the one-seat rule
     * @return array
     */
    public static function execute(int $cmid, array $items, string $tabid = ''): array {
        global $USER;

        ['cmid' => $cmid, 'items' => $items, 'tabid' => $tabid] = self::validate_parameters(
            self::execute_parameters(),
            ['cmid' => $cmid, 'items' => $items, 'tabid' => $tabid]
        );
        $access = lab_access::from_cmid($cmid);
        self::validate_context($access->context);
        // Only the owner of the lab uncovers anything: no other user can be named.
        [$userid] = $access->resolve_user(0);
        // What a second login "uncovers" would end up in the student's mask (docs/seat.md).
        seat::require_seat($access, $userid, $tabid);

        $client = \core\di::get(manager_client::class);
        $session = $client->find_session($userid, (int) $access->instance->id);
        if ($session === null || $session->status !== 'running') {
            throw new \moodle_exception('sessionnotrunning', 'gns3lab');
        }
        return ['added' => reveal_store::add($access->instance, (int) $USER->id, $session, $items)];
    }

    /**
     * Result.
     *
     * @return external_single_structure
     */
    public static function execute_returns(): external_single_structure {
        return new external_single_structure([
            'added' => new external_value(PARAM_INT, 'Items that were not in the mask before'),
        ]);
    }
}
