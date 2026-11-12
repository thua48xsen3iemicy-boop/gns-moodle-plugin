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
 * Stops the labs their students have left (docs/idle-stop.md).
 *
 * The lab page of the owner beats every few seconds (docs/seat.md) and the seat row keeps the time of the last beat
 * after the page is gone. A lab whose owner has had no lab page open for longer than the site setting `idletimeout`
 * is stopped, as if the student pressed "Stop": the slot is free for somebody else. A page that is merely in the
 * background still beats, so only a closed page, a closed browser, a signed-out or sleeping computer count.
 *
 * Sessions are not stored in Moodle. The seat row remembers the beat it was last looked at (`timeidlechecked`), so
 * the lab manager is asked once for every time a student leaves, not at every run of the task.
 *
 * Activities without the one-seat rule (`seatmode` off) are left alone: their pages do not beat. Authoring sessions
 * are not labs of an activity and are not looked at either.
 *
 * @package    mod_gns3lab
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
class idle_labs {
    /** Default of the site setting, minutes. */
    public const DEFAULT_TIMEOUT = 30;

    /** The shortest timeout that is honoured, minutes: a page frozen by the browser must not cost the student the lab. */
    public const MIN_TIMEOUT = 5;

    /** Seat rows older than this are not looked at, seconds: no lab lives that long (the manager's own limit). */
    public const LOOKBACK = 8 * DAYSECS;

    /** After this many failed requests in a row the run ends: the lab manager is down, the next run tries again. */
    public const MAX_FAILURES = 3;

    /**
     * The timeout from the site settings.
     *
     * @return int seconds, 0 when labs are not stopped for idleness
     */
    public static function timeout(): int {
        $minutes = get_config('gns3lab', 'idletimeout');
        $minutes = $minutes === false || $minutes === null || $minutes === '' ? self::DEFAULT_TIMEOUT : (int) $minutes;
        if ($minutes <= 0) {
            return 0;
        }
        return max($minutes, self::MIN_TIMEOUT) * MINSECS;
    }

    /**
     * Stops the labs left for longer than the timeout.
     *
     * @param manager_client $client manager client
     * @param int|null $now current time, null for now
     * @return int number of labs stopped
     */
    public static function stop_idle(manager_client $client, ?int $now = null): int {
        global $DB;

        $timeout = self::timeout();
        if ($timeout === 0) {
            return 0;
        }
        $now ??= time();
        $cutoff = $now - $timeout;
        $rows = $DB->get_records_sql(
            "SELECT s.id, s.gns3labid, s.userid, s.timemodified
               FROM {gns3lab_seat} s
               JOIN {gns3lab} g ON g.id = s.gns3labid
              WHERE g.seatmode <> :off AND s.timemodified < :cutoff AND s.timemodified > :oldest
                    AND s.timeidlechecked < s.timemodified
           ORDER BY s.timemodified",
            ['off' => seat::OFF, 'cutoff' => $cutoff, 'oldest' => $now - self::LOOKBACK]
        );
        $stopped = 0;
        $failures = 0;
        foreach ($rows as $row) {
            try {
                $stopped += (int) self::stop_one($client, $row, $cutoff);
                $failures = 0;
            } catch (manager_exception $e) {
                // The row stays unchecked: the next run asks again.
                mtrace("mod_gns3lab: idle lab of user {$row->userid} in activity {$row->gns3labid}: " . $e->remotecode);
                if (++$failures >= self::MAX_FAILURES) {
                    break;
                }
            }
        }
        return $stopped;
    }

    /**
     * Looks at the lab behind one seat row and stops it when it is still alive.
     *
     * @param manager_client $client manager client
     * @param \stdClass $row seat row: id, gns3labid, userid, timemodified
     * @param int $cutoff a beat older than this means the student has left
     * @return bool the lab was stopped
     */
    protected static function stop_one(manager_client $client, \stdClass $row, int $cutoff): bool {
        global $DB;

        $session = $client->find_session((int) $row->userid, (int) $row->gns3labid);
        $live = $session !== null && !in_array($session->status, session_data::FINAL_STATUSES, true);
        $stopped = false;
        if ($live && $session->status !== 'stopping') {
            // The student may have come back while the manager was asked.
            $seen = (int) $DB->get_field('gns3lab_seat', 'timemodified', ['id' => $row->id]);
            if ($seen >= $cutoff) {
                return false;
            }
            $client->stop_session($session->session_id);
            activity_sessions::forget((int) $row->gns3labid);
            self::log_stopped($row, (int) $seen);
            $stopped = true;
        }
        // Remember the beat that was looked at: a later beat (the student is back) makes the row a candidate again.
        $DB->set_field('gns3lab_seat', 'timeidlechecked', (int) $row->timemodified, ['id' => $row->id]);
        return $stopped;
    }

    /**
     * Writes the stop to the log of the course.
     *
     * @param \stdClass $row seat row
     * @param int $seen when the lab page of the student was last seen
     */
    protected static function log_stopped(\stdClass $row, int $seen): void {
        $cm = get_coursemodule_from_instance('gns3lab', (int) $row->gns3labid);
        if (!$cm) {
            return;
        }
        \mod_gns3lab\event\lab_idle_stopped::create([
            'objectid' => (int) $row->gns3labid,
            'context' => \core\context\module::instance($cm->id),
            'relateduserid' => (int) $row->userid,
            'other' => ['lastseen' => $seen],
        ])->trigger();
    }
}
