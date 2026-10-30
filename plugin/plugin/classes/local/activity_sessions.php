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
 * Sessions of all students of an activity for the course page of a teacher, kept for a few seconds in the application cache.
 *
 * Several teachers and a poll every 15 seconds would otherwise ask the manager once each. An answer, or the failure to
 * get one, is used as is for FRESH seconds. Only the short summaries of session_data::summary() are kept, by user id.
 *
 * @package    mod_gns3lab
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
class activity_sessions {
    /** How long an entry is used without asking the manager, seconds. */
    public const FRESH = 10;

    /**
     * Sessions of an activity.
     *
     * @param manager_client $client manager client
     * @param int $instanceid gns3lab instance id
     * @return \stdClass sessions: summaries by user id (array), error: code of the failure or an empty string
     */
    public static function get(manager_client $client, int $instanceid): \stdClass {
        $cache = \cache::make('mod_gns3lab', 'activitysessions');
        $key = self::key($instanceid);
        $entry = $cache->get($key);
        if ($entry && time() - $entry->fetchedat < self::FRESH) {
            return $entry;
        }
        $entry = (object) ['sessions' => [], 'error' => '', 'fetchedat' => time()];
        try {
            foreach ($client->list_activity_sessions($instanceid) as $session) {
                if (isset($session->user_id) && is_int($session->user_id)) {
                    $entry->sessions[$session->user_id] = session_data::summary($session);
                }
            }
        } catch (manager_exception $e) {
            $entry->sessions = [];
            $entry->error = clean_param($e->remotecode, PARAM_ALPHANUMEXT);
            debugging('mod_gns3lab: no sessions of the activity from the lab manager: ' . $e->debuginfo, DEBUG_DEVELOPER);
        }
        $cache->set($key, $entry);
        return $entry;
    }

    /**
     * Drops the cached sessions of an activity.
     *
     * @param int $instanceid gns3lab instance id
     */
    public static function forget(int $instanceid): void {
        \cache::make('mod_gns3lab', 'activitysessions')->delete(self::key($instanceid));
    }

    /**
     * Cache key of an activity: it includes the manager, so that a changed address does not show old sessions.
     *
     * @param int $instanceid gns3lab instance id
     * @return string
     */
    protected static function key(int $instanceid): string {
        $baseurl = rtrim(trim((string) get_config('gns3lab', 'managerurl')), '/');
        return 'a' . sha1($baseurl . "\n" . $instanceid);
    }
}
