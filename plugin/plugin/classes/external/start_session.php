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
use mod_gns3lab\local\lab_library;
use mod_gns3lab\local\manager_client;
use mod_gns3lab\local\manager_exception;
use mod_gns3lab\local\seat;
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
            'tabid' => new external_value(PARAM_ALPHANUMEXT, 'Id of this page load (one-seat rule)', VALUE_DEFAULT, ''),
        ]);
    }

    /**
     * Starts the lab.
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
        // Only the page that holds the seat of the student starts the lab (docs/seat.md).
        seat::require_seat($access, $userid, $tabid);

        $lab = lab_library::activity_lab($access->instance);
        if ($lab === null) {
            throw new \moodle_exception('nolabref', 'gns3lab');
        }
        try {
            $session = \core\di::get(manager_client::class)->create_session(
                $userid,
                (int) $access->instance->id,
                (int) $access->course->id,
                $lab,
                (int) $access->instance->maxduration
            );
        } catch (manager_exception $e) {
            if ($e->remotecode === 'session_conflict') {
                self::conflict($e);
            }
            if ($e->remotecode === 'lab_invalid' && is_array($lab)) {
                self::rejected($access, $e);
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
     * Reports a library lab the lab manager refused (422 with problems): to the log, and to the user as a lab
     * set up with errors. An image was removed or the site limits were cut after the lab was published.
     *
     * @param lab_access $access the activity
     * @param manager_exception $e lab_invalid from the lab manager
     */
    protected static function rejected(lab_access $access, manager_exception $e): void {
        $problems = array_map(
            fn($p) => preg_replace('/[^A-Za-z0-9_.\[\]-]/', '', $p['path']) . ': ' . $p['code'],
            lab_problems::from_exception($e)
        );
        $event = \mod_gns3lab\event\lab_rejected::create([
            'objectid' => $access->instance->id,
            'context' => $access->context,
            'other' => [
                'labversionid' => (int) $access->instance->labversionid,
                'problems' => \core_text::substr(implode('; ', $problems), 0, 1000),
            ],
        ]);
        $event->trigger();
        throw new \moodle_exception('labdefrejected', 'gns3lab', '', null, implode('; ', $problems));
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
