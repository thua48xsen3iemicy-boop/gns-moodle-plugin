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

use mod_gns3lab\local\manager_client;
use mod_gns3lab\local\manager_exception;
use mod_gns3lab\local\purges;

/**
 * Daily safety net: the lab manager deletes the work of activities this site no longer has (manager CONTRACTS §2.10).
 *
 * Covers a purge request that never arrived, for example after this site's database was restored from a backup.
 *
 * @package    mod_gns3lab
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
class reconcile_purges extends \core\task\scheduled_task {
    /**
     * Name of the task.
     *
     * @return string
     */
    public function get_name(): string {
        return get_string('taskreconcilepurges', 'gns3lab');
    }

    /**
     * Runs the task.
     */
    public function execute(): void {
        if (!manager_client::is_configured()) {
            return;
        }
        try {
            $result = purges::reconcile(\core\di::get(manager_client::class));
        } catch (manager_exception $e) {
            mtrace('mod_gns3lab: reconciliation with the lab manager failed: ' . $e->remotecode);
            return;
        }
        if ($result !== null && $result->workspaces) {
            mtrace("mod_gns3lab: the lab manager deletes {$result->workspaces} workspaces of deleted activities");
        }
    }
}
