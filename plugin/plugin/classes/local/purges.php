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

/**
 * Deleting student work on the lab manager (manager CONTRACTS §2.10).
 *
 * The projects of the students live on the lab manager and its CephFS, not in Moodle. When an activity, a library lab
 * or a user is deleted here, the manager must be told, or the storage fills up with work nobody can reach. Moodle must
 * not wait for the manager or fail without it: the request goes into the table gns3lab_purges, and the task
 * send_purges delivers it, retrying until the manager has taken it. A student's own "Wipe" is the exception: the
 * student waits for the answer, so it is sent at once.
 *
 * @package    mod_gns3lab
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
class purges {
    /** Table of the requests not delivered yet. */
    public const TABLE = 'gns3lab_purges';
    /** Shortest pause after a failed delivery, seconds; it doubles up to RETRY_MAX. */
    public const RETRY_MIN = 60;
    /** Longest pause between two deliveries of one request, seconds. */
    public const RETRY_MAX = 3600;
    /** Requests one run of the task delivers at most. */
    public const BATCH = 200;
    /** Session statuses of a lab that no longer runs (manager CONTRACTS §2.3). */
    public const FINISHED = ['stopped', 'failed'];

    /**
     * Queues a request. Ids left at 0 are not part of it; a request already queued is not repeated.
     *
     * @param int $instanceid gns3lab instance id
     * @param int $userid user id
     * @param int $librarylabid lab of the lab library
     */
    public static function queue(int $instanceid = 0, int $userid = 0, int $librarylabid = 0): void {
        global $DB;

        if (($instanceid <= 0 && $userid <= 0 && $librarylabid <= 0) || !manager_client::is_configured()) {
            // Without a lab manager there is no work to delete; the daily reconciliation covers a manager set up later.
            return;
        }
        $key = ['instanceid' => max(0, $instanceid), 'userid' => max(0, $userid), 'librarylabid' => max(0, $librarylabid)];
        if ($DB->record_exists(self::TABLE, $key)) {
            return;
        }
        $now = time();
        $DB->insert_record(self::TABLE, (object) ($key + [
            'attempts' => 0,
            'nextattempt' => $now,
            'lasterror' => '',
            'timecreated' => $now,
        ]));
    }

    /**
     * Delivers the requests that are due. A request the manager has taken is removed; a failed one waits longer.
     *
     * @param manager_client $client lab manager
     * @param int $now current time
     * @return int requests delivered
     */
    public static function send_pending(manager_client $client, int $now): int {
        global $DB;

        $rows = $DB->get_records_select(self::TABLE, 'nextattempt <= :now', ['now' => $now], 'nextattempt, id', '*', 0,
            self::BATCH);
        if (!$rows) {
            return 0;
        }
        if (!$client->has_feature('purge')) {
            // A manager older than 0.19.0: keep the requests until it is updated.
            foreach ($rows as $row) {
                self::postpone($row, 'nopurge', $now);
            }
            return 0;
        }
        $sent = 0;
        foreach ($rows as $row) {
            try {
                $client->purge((int) $row->instanceid, (int) $row->userid, (int) $row->librarylabid);
            } catch (manager_exception $e) {
                self::postpone($row, $e->remotecode, $now);
                continue;
            }
            $DB->delete_records(self::TABLE, ['id' => $row->id]);
            $sent++;
        }
        return $sent;
    }

    /**
     * Moves a request whose delivery failed to a later time.
     *
     * @param \stdClass $row request
     * @param string $error why it failed
     * @param int $now current time
     */
    protected static function postpone(\stdClass $row, string $error, int $now): void {
        global $DB;

        $attempts = (int) $row->attempts;
        $DB->update_record(self::TABLE, (object) [
            'id' => $row->id,
            'attempts' => $attempts + 1,
            'nextattempt' => $now + min(self::RETRY_MAX, self::RETRY_MIN * 2 ** min($attempts, 10)),
            'lasterror' => \core_text::substr($error, 0, 64),
        ]);
    }

    /**
     * Daily safety net: tells the manager which activities exist, so it deletes the work of the others.
     *
     * @param manager_client $client lab manager
     * @return \stdClass|null the answer, null when there was nothing to send
     */
    public static function reconcile(manager_client $client): ?\stdClass {
        global $DB;

        if (!$client->has_feature('purge')) {
            return null;
        }
        $ids = array_map('intval', $DB->get_fieldset_select('gns3lab', 'id', '1 = 1'));
        if (!$ids) {
            // The manager refuses an empty list: an error of ours must never delete everything.
            return null;
        }
        return $client->reconcile_purges($ids);
    }

    /**
     * Whether the student may wipe the lab here: the capabilities and a lab manager that deletes work.
     *
     * @param \context_module $context activity context
     * @param manager_client $client lab manager
     * @return bool
     */
    public static function can_wipe(\context_module $context, manager_client $client): bool {
        if (!manager_client::is_configured() || !has_all_capabilities(['mod/gns3lab:use', 'mod/gns3lab:wipe'], $context)) {
            return false;
        }
        try {
            return $client->has_feature('purge');
        } catch (manager_exception $e) {
            return false;
        }
    }

    /**
     * Wipes the student's own lab in the activity: the manager deletes the project, the next start is a fresh one.
     *
     * A running lab is not wiped: the student stops it first, so nothing is lost by a click on the wrong page. What the
     * student uncovered on the scheme belongs to the deleted project and goes too; check runs and grades stay.
     *
     * The manager deletes the check runs of the workspace with it (CONTRACTS §2.10). A run Moodle still has as queued or
     * running would then be found gone and given up as lost, and a mark the manager had already set would never reach
     * the gradebook. So the runs are synced first, and the lab is not wiped while one of them is still unresolved.
     *
     * @param \stdClass $instance gns3lab record
     * @param int $userid the student
     * @param manager_client $client lab manager
     * @return \stdClass {workspaces, pending}
     * @throws \moodle_exception wipestopfirst when the lab is running, wipecheckpending when a check run is unresolved
     * @throws manager_exception when the manager cannot be asked about the session or a check run
     */
    public static function wipe(\stdClass $instance, int $userid, manager_client $client): \stdClass {
        $session = $client->find_session($userid, (int) $instance->id);
        if ($session !== null && !in_array($session->status ?? '', self::FINISHED, true)) {
            throw new \moodle_exception('wipestopfirst', 'gns3lab');
        }
        check_runs::sync($instance, $userid, $client);
        if (check_runs::has_pending((int) $instance->id, $userid)) {
            throw new \moodle_exception('wipecheckpending', 'gns3lab');
        }
        $result = $client->purge((int) $instance->id, $userid);
        reveal::delete((int) $instance->id, [$userid]);
        return $result;
    }

    /**
     * Observer of user_deleted: the user's work on the lab manager goes too.
     *
     * @param \core\event\user_deleted $event the event
     */
    public static function user_deleted(\core\event\user_deleted $event): void {
        self::queue(0, (int) $event->objectid);
    }
}
