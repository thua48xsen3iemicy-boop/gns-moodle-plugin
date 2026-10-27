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
use mod_gns3lab\local\seat;

/**
 * Claims the seat of the current user in an activity, or reports who holds it (docs/seat.md).
 *
 * The lab page calls it when it opens (`takeover`), when the student presses "use here" (`takeover`), and every few
 * seconds afterwards (a beat, no `takeover`). A beat only keeps the seat alive or picks it up when it is free; it never
 * takes it from another page, so two pages of one login do not fight over it.
 *
 * Only the owner of the lab has a seat: an observer is never asked, and no other user can be named.
 *
 * @package    mod_gns3lab
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
class claim_seat extends external_api {
    /**
     * Parameters.
     *
     * @return external_function_parameters
     */
    public static function execute_parameters(): external_function_parameters {
        return new external_function_parameters([
            'cmid' => new external_value(PARAM_INT, 'Course module id'),
            'tabid' => new external_value(PARAM_ALPHANUMEXT, 'Random id of this page load, made by the browser'),
            'takeover' => new external_value(
                PARAM_BOOL,
                'The page was just opened or the student chose to use it here: take the seat from another page of the same login',
                VALUE_DEFAULT,
                false
            ),
        ]);
    }

    /**
     * Claims the seat.
     *
     * @param int $cmid course module id
     * @param string $tabid page id
     * @param bool $takeover take the seat from another page of the same login
     * @return array
     */
    public static function execute(int $cmid, string $tabid, bool $takeover = false): array {
        ['cmid' => $cmid, 'tabid' => $tabid, 'takeover' => $takeover] = self::validate_parameters(
            self::execute_parameters(),
            ['cmid' => $cmid, 'tabid' => $tabid, 'takeover' => $takeover]
        );
        $access = lab_access::from_cmid($cmid);
        self::validate_context($access->context);
        [$userid] = $access->resolve_user(0);

        $result = seat::claim($access->instance, $userid, seat::clean_tabid($tabid), $takeover);
        if ($takeover) {
            // Pages that ask on their own every few seconds are not logged, only a page that was just opened.
            seat::log_denied($access, $userid, $result['status'], 'page');
        }
        return [
            'status' => $result['status'],
            'mode' => seat::mode($access->instance),
            'idle' => $result['idle'],
            'beat' => seat::BEAT,
        ];
    }

    /**
     * Result.
     *
     * @return external_single_structure
     */
    public static function execute_returns(): external_single_structure {
        return new external_single_structure([
            'status' => new external_value(PARAM_ALPHA, 'held, othertab or otherlogin'),
            'mode' => new external_value(PARAM_ALPHA, 'Seat mode of the activity: tab, login or off'),
            'idle' => new external_value(PARAM_INT, 'Seconds since the holder was last seen, 0 when the caller holds the seat'),
            'beat' => new external_value(PARAM_INT, 'Seconds between the beats of the page'),
        ]);
    }
}
