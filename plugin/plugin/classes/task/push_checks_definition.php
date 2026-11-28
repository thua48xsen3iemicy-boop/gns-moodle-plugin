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

use mod_gns3lab\local\checks;
use mod_gns3lab\local\manager_client;
use mod_gns3lab\local\manager_exception;

/**
 * Hands the changed auto-checks of an activity to its running labs (docs/autochecks.md §12, CONTRACTS §2.9).
 *
 * A lab gets the checks of its activity when it starts; an edit reaches the live ones here, so the next check and the
 * teacher's forecast follow it. A lab that runs an earlier version of the lab, where the checks do not fit, keeps the
 * ones it has until it is started again (checks_invalid); a lab that stopped meanwhile needs nothing. The page of the
 * check asks again before each check (session_checks::sync()), so a lost run of this task loses nothing but time.
 *
 * @package    mod_gns3lab
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
class push_checks_definition extends \core\task\adhoc_task {
    /** Session statuses of a live lab. */
    public const LIVE = ['queued', 'starting', 'running', 'stopping'];

    /**
     * Queues the task for an activity; one is enough for any number of edits.
     *
     * @param int $instanceid gns3lab instance id
     */
    public static function queue(int $instanceid): void {
        $task = new self();
        $task->set_custom_data(['instanceid' => $instanceid]);
        \core\task\manager::queue_adhoc_task($task, true);
    }

    /**
     * Runs the task.
     */
    public function execute(): void {
        global $DB;

        $instanceid = (int) ($this->get_custom_data()->instanceid ?? 0);
        $instance = $DB->get_record('gns3lab', ['id' => $instanceid]);
        if (!$instance || !manager_client::is_configured()) {
            return;
        }
        $client = \core\di::get(manager_client::class);
        if (!$client->has_feature('activity-checks')) {
            return;
        }
        $doc = checks::activity_document($instance);
        foreach ($client->list_activity_sessions($instanceid) as $session) {
            if (!in_array($session->status, self::LIVE, true)) {
                continue;
            }
            try {
                $client->put_checks_definition($session->session_id, $doc);
            } catch (manager_exception $e) {
                if (!in_array($e->remotecode, ['checks_invalid', 'session_not_running', 'session_not_found'], true)) {
                    throw $e; // The manager is away: the task is tried again later.
                }
                mtrace("mod_gns3lab: checks of activity {$instanceid} not given to session {$session->session_id}: "
                    . $e->remotecode);
            }
        }
    }
}
