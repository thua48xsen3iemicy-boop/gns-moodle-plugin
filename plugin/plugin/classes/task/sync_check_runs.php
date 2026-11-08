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

namespace mod_gns3lab\task;

use mod_gns3lab\local\check_runs;
use mod_gns3lab\local\manager_client;
use mod_gns3lab\local\manager_exception;

/**
 * Finishes the check runs nobody watched to the end (docs/autochecks.md).
 *
 * A student may close the lab page while a check runs. The page and the external functions record the result when they
 * next ask; this task asks the lab manager for the runs that are still pending after a minute, so the grade reaches
 * the gradebook without the student coming back. Runs pending for a day are given up.
 *
 * @package    mod_gns3lab
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
class sync_check_runs extends \core\task\scheduled_task {
    /** Runs younger than this are left to the page that started them, seconds. */
    public const MIN_AGE = 60;

    /**
     * Name of the task.
     *
     * @return string
     */
    public function get_name(): string {
        return get_string('tasksynccheckruns', 'gns3lab');
    }

    /**
     * Runs the task.
     */
    public function execute(): void {
        global $DB;

        $now = time();
        check_runs::give_up_stale($now);
        if (!manager_client::is_configured()) {
            return;
        }
        [$insql, $params] = $DB->get_in_or_equal(check_runs::PENDING, SQL_PARAMS_NAMED);
        $pairs = $DB->get_records_sql(
            "SELECT DISTINCT " . $DB->sql_concat('r.gns3labid', "'-'", 'r.userid') . " AS pair, r.gns3labid, r.userid
               FROM {" . check_runs::TABLE . "} r
              WHERE r.status {$insql} AND r.timecreated < :before",
            $params + ['before' => $now - self::MIN_AGE]
        );
        $client = \core\di::get(manager_client::class);
        $instances = [];
        foreach ($pairs as $pair) {
            $labid = (int) $pair->gns3labid;
            $instances[$labid] ??= $DB->get_record('gns3lab', ['id' => $labid]);
            if (!$instances[$labid]) {
                continue;
            }
            try {
                check_runs::sync($instances[$labid], (int) $pair->userid, $client);
            } catch (manager_exception $e) {
                mtrace("mod_gns3lab: check runs of user {$pair->userid} in activity {$labid}: " . $e->remotecode);
            }
        }
    }
}
