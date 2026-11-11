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


namespace mod_gns3lab\local;

use core_external\external_single_structure;
use core_external\external_value;

/**
 * Maintenance mode of the lab manager (manager CONTRACTS §2.11): what the pages tell the users.
 *
 * The operator announces it on the manager; the plugin only reads it. Phases: off, announced (labs stop at startsat,
 * new labs are refused), draining (the labs are being stopped), active (nothing runs, the operator works).
 *
 * @package    mod_gns3lab
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
class maintenance {
    /** Phases the manager reports; anything else is read as off. */
    public const STATES = ['off', 'announced', 'draining', 'active'];

    /**
     * Description for execute_returns().
     *
     * @return external_single_structure
     */
    public static function structure(): external_single_structure {
        return new external_single_structure([
            'state' => new external_value(PARAM_ALPHA, 'off, announced, draining or active'),
            'startsat' => new external_value(PARAM_INT, 'When the labs are stopped, 0 if off'),
            'message' => new external_value(PARAM_TEXT, 'Text of the administrator, may be empty'),
        ], 'Maintenance mode of the lab manager', VALUE_DEFAULT, ['state' => 'off', 'startsat' => 0, 'message' => '']);
    }

    /**
     * Converts the maintenance object of the manager.
     *
     * @param mixed $value object from GET /api/v1/maintenance or the session field, or null
     * @return array state, startsat, message
     */
    public static function export(mixed $value): array {
        $off = ['state' => 'off', 'startsat' => 0, 'message' => ''];
        if (!$value instanceof \stdClass || !in_array($value->state ?? null, self::STATES, true) || $value->state === 'off') {
            return $off;
        }
        $startsat = is_string($value->starts_at ?? null) ? strtotime($value->starts_at) : false;
        return [
            'state' => $value->state,
            'startsat' => $startsat === false ? 0 : $startsat,
            'message' => is_string($value->message ?? null) ? \core_text::substr($value->message, 0, 500) : '',
        ];
    }

    /**
     * The maintenance as a page sees it: from the session when there is one, else asked from the manager.
     *
     * A manager that does not answer must not break the page: its maintenance reads as off.
     *
     * @param manager_client $client
     * @param \stdClass|null $session session from the manager: it carries the field while the mode is on
     * @return array state, startsat, message
     */
    public static function current(manager_client $client, ?\stdClass $session = null): array {
        if ($session !== null && isset($session->maintenance)) {
            return self::export($session->maintenance);
        }
        try {
            return self::export($client->get_maintenance());
        } catch (\moodle_exception $e) {
            return self::export(null);
        }
    }

    /**
     * A Moodle notification for the pages without a live session bar (activity, course, library), or ''.
     *
     * @return string HTML
     */
    public static function notification(): string {
        global $OUTPUT;
        if (!manager_client::is_configured()) {
            return '';
        }
        $state = self::current(\core\di::get(manager_client::class));
        if ($state['state'] === 'off') {
            return '';
        }
        if ($state['state'] === 'announced') {
            $text = get_string('maintenanceannouncedat', 'gns3lab', userdate($state['startsat'],
                get_string('strftimedatetimeshort', 'core_langconfig')));
        } else {
            $text = get_string('maintenanceactive', 'gns3lab');
        }
        if ($state['message'] !== '') {
            $text .= ' ' . get_string('maintenancenote', 'gns3lab', $state['message']);
        }
        return $OUTPUT->notification(s($text), \core\output\notification::NOTIFY_WARNING, false);
    }
}
