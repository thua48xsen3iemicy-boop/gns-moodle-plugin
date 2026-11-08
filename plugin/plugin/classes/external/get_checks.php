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
use mod_gns3lab\local\check_runs;
use mod_gns3lab\local\checks;
use mod_gns3lab\local\lab_access;
use mod_gns3lab\local\manager_client;
use mod_gns3lab\local\manager_exception;

/**
 * Auto-checks of the user or of an observed student in an activity: the settings the page needs, the attempts and the
 * runs, the newest first (docs/autochecks.md).
 *
 * Runs that are not over are asked of the lab manager first, so a run that ended while nobody looked is graded here.
 *
 * @package    mod_gns3lab
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
class get_checks extends external_api {
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
     * Lists the checks.
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
        [$userid, $readonly] = $access->resolve_user($userid);
        return self::overview($access, $userid, $readonly, true);
    }

    /**
     * The answer of this function, also returned by start_check.
     *
     * @param lab_access $access the activity
     * @param int $userid owner of the lab
     * @param bool $readonly a teacher looks at the lab of a student
     * @param bool $sync ask the manager about the runs that are not over
     * @return array
     */
    public static function overview(lab_access $access, int $userid, bool $readonly, bool $sync): array {
        $instance = $access->instance;
        $enabled = checks::enabled($instance);
        $available = false;
        if ($enabled && manager_client::is_configured()) {
            $client = \core\di::get(manager_client::class);
            try {
                $available = $client->has_feature('checks');
                if ($available && $sync) {
                    check_runs::sync($instance, $userid, $client);
                }
            } catch (manager_exception $e) {
                // The runs are shown as recorded; the page asks again later.
                $available = $available && $e->remotecode !== 'notconfigured';
            }
        }
        $used = check_runs::attempts_used((int) $instance->id, $userid);
        $left = checks::attempts_left($instance, $used);
        $from = checks::details_from($instance);
        return [
            'enabled' => $enabled,
            'available' => $enabled && $available,
            'canstart' => $enabled && $available && !$readonly && $left !== 0
                && has_capability('mod/gns3lab:use', $access->context),
            'report' => checks::report_mode($instance),
            'detailsfrom' => $readonly ? 0 : ($from === PHP_INT_MAX ? -1 : $from),
            'attempts' => ['max' => checks::max_attempts($instance), 'used' => $used, 'left' => $left],
            'runs' => $enabled ? array_map([check_runs::class, 'export'], check_runs::rows((int) $instance->id, $userid)) : [],
        ];
    }

    /**
     * Result.
     *
     * @return external_single_structure
     */
    public static function execute_returns(): external_single_structure {
        return new external_single_structure([
            'enabled' => new external_value(PARAM_BOOL, 'The activity has auto-checks switched on'),
            'available' => new external_value(PARAM_BOOL, 'Switched on and the lab manager offers checks'),
            'canstart' => new external_value(PARAM_BOOL, 'The current user may start a check of this lab now, if it runs'),
            'report' => new external_value(PARAM_ALPHA, 'What the student sees: full, counts or after'),
            'detailsfrom' => new external_value(PARAM_INT, 'From when the findings are shown: 0 always, -1 never'),
            'attempts' => new external_single_structure([
                'max' => new external_value(PARAM_INT, 'Most graded attempts, 0 for unlimited'),
                'used' => new external_value(PARAM_INT, 'Graded attempts so far'),
                'left' => new external_value(PARAM_INT, 'Graded attempts left, -1 for unlimited'),
            ]),
            'runs' => new external_multiple_structure(
                new external_single_structure(check_runs::summary_fields()),
                'Runs of this user in this activity, the newest first'
            ),
        ]);
    }
}
