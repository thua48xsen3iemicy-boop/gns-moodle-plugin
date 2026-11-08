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

/**
 * One check run with its report: the page polls it while the run goes on and shows the report when it is over.
 *
 * The run must be one of this user in this activity, as recorded by the plugin; the session is the one of the record,
 * the browser names only the run. The findings and rules are left out for a student when the activity shows only the
 * counts (or not yet the findings): the server decides, not the page. A teacher who observes always gets them.
 *
 * @package    mod_gns3lab
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
class get_check_run extends external_api {
    /**
     * Parameters.
     *
     * @return external_function_parameters
     */
    public static function execute_parameters(): external_function_parameters {
        return new external_function_parameters([
            'cmid' => new external_value(PARAM_INT, 'Course module id'),
            'runid' => new external_value(PARAM_ALPHANUMEXT, 'Run id'),
            'userid' => new external_value(PARAM_INT, 'Whose lab, 0 for the current user', VALUE_DEFAULT, 0),
        ]);
    }

    /**
     * Reads the run.
     *
     * @param int $cmid course module id
     * @param string $runid run id
     * @param int $userid whose lab, 0 for the current user
     * @return array
     */
    public static function execute(int $cmid, string $runid, int $userid = 0): array {
        ['cmid' => $cmid, 'runid' => $runid, 'userid' => $userid] = self::validate_parameters(
            self::execute_parameters(),
            ['cmid' => $cmid, 'runid' => $runid, 'userid' => $userid]
        );
        $access = lab_access::from_cmid($cmid);
        self::validate_context($access->context);
        require_capability('mod/gns3lab:view', $access->context);
        [$userid, $readonly] = $access->resolve_user($userid);
        $instance = $access->instance;
        if (!checks::enabled($instance)) {
            throw new \moodle_exception('checksdisabled', 'gns3lab');
        }
        $row = check_runs::get((int) $instance->id, $userid, $runid);
        if ($row === null) {
            throw new \moodle_exception('checknotfound', 'gns3lab');
        }
        [$row, $run] = check_runs::refresh($instance, $row, \core\di::get(manager_client::class));

        $visible = checks::details_visible($instance, $readonly);
        $from = checks::details_from($instance);
        $details = check_runs::export_details($visible ? $run : null);
        return check_runs::export($row) + [
            'detailed' => $visible && $run !== null,
            'detailsfrom' => $readonly ? 0 : ($from === PHP_INT_MAX ? -1 : $from),
            'gone' => $run === null,
        ] + $details;
    }

    /**
     * Result.
     *
     * @return external_single_structure
     */
    public static function execute_returns(): external_single_structure {
        return new external_single_structure(check_runs::summary_fields() + [
            'detailed' => new external_value(PARAM_BOOL, 'Findings and rules are included'),
            'detailsfrom' => new external_value(PARAM_INT, 'From when the findings are shown: 0 always, -1 never'),
            'gone' => new external_value(PARAM_BOOL, 'The lab manager no longer has the run (its session was deleted)'),
        ] + check_runs::details_fields());
    }
}
