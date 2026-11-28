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
 * The teacher's forecast of a student's lab (docs/teacher-dashboard.md §14, manager CONTRACTS §2.9a).
 *
 * The lab manager grades the last facts snapshot of a session by the auto-check rules, without the teacher's scripts: the
 * teacher sees how far a student got who never ran a check. A forecast is never shown to the student and never goes to the
 * gradebook. Answers are kept for activity_sessions::FRESH seconds per activity and grading, like the sessions.
 *
 * @package    mod_gns3lab
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
class forecasts {
    /**
     * Whether the forecasts of an activity can be asked for.
     *
     * @param manager_client $client manager client
     * @param \stdClass $instance gns3lab record
     * @return bool
     */
    public static function available(manager_client $client, \stdClass $instance): bool {
        return !empty($instance->checksenabled) && $client->has_feature('forecast');
    }

    /**
     * Forecasts of the students of an activity.
     *
     * @param manager_client $client manager client
     * @param \stdClass $instance gns3lab record with the auto-check settings
     * @param array $ids user id => session id (activity_sessions entry `ids`), only the students the caller may observe
     * @return array user id => forecast (structure()) for the students with one
     */
    public static function of_activity(manager_client $client, \stdClass $instance, array $ids): array {
        if (!$ids || !self::available($client, $instance)) {
            return [];
        }
        $grading = checks::grading($instance);
        $cache = \cache::make('mod_gns3lab', 'activitysessions');
        $key = self::key((int) $instance->id, $grading);
        $entry = $cache->get($key);
        if (!$entry || time() - $entry->fetchedat >= activity_sessions::FRESH || array_diff($ids, array_keys($entry->bysession))) {
            $entry = (object) ['bysession' => array_fill_keys(array_values($ids), null), 'fetchedat' => time()];
            try {
                foreach ($client->get_forecasts(array_values($ids), $grading) as $sessionid => $forecast) {
                    $entry->bysession[$sessionid] = $forecast ? self::export($forecast, $grading, $instance) : null;
                }
            } catch (manager_exception $e) {
                debugging('mod_gns3lab: no forecasts from the lab manager: ' . $e->debuginfo, DEBUG_DEVELOPER);
                return [];
            }
            $cache->set($key, $entry);
        }
        $out = [];
        foreach ($ids as $userid => $sessionid) {
            if (!empty($entry->bysession[$sessionid])) {
                $out[(int) $userid] = $entry->bysession[$sessionid];
            }
        }
        return $out;
    }

    /**
     * A forecast of the manager as the page gets it.
     *
     * @param \stdClass $forecast forecast of POST /forecasts
     * @param array $grading the grading it was asked with
     * @param \stdClass|null $instance the activity, to tell a forecast by earlier checks (docs/autochecks.md §12)
     * @return array
     */
    public static function export(\stdClass $forecast, array $grading, ?\stdClass $instance = null): array {
        $thresholds = $grading['thresholds'];
        return [
            'mark' => (int) $forecast->mark,
            'topmark' => $thresholds ? (int) $thresholds[0]['mark'] : (int) $grading['fail_mark'],
            'failmark' => (int) $grading['fail_mark'],
            'incomplete' => ($forecast->outcome ?? '') === 'incomplete',
            'scriptsignored' => !empty($forecast->scripts_ignored),
            'outdated' => $instance !== null && checks::outdated($instance, is_string($forecast->checks_def ?? null)
                ? $forecast->checks_def : null),
            'assessedat' => self::timestamp($forecast->assessed_at ?? null),
        ];
    }

    /**
     * Description of a forecast of a cell for execute_returns().
     *
     * @return external_single_structure
     */
    public static function structure(): external_single_structure {
        return new external_single_structure([
            'mark' => new external_value(PARAM_INT, 'Mark the last facts snapshot would get from an auto-check'),
            'topmark' => new external_value(PARAM_INT, 'Top mark of the grading of the activity'),
            'failmark' => new external_value(PARAM_INT, 'Fail mark of the grading of the activity'),
            'incomplete' => new external_value(PARAM_BOOL, 'Some objects of the focus of the lab could not be checked'),
            'scriptsignored' => new external_value(PARAM_BOOL, 'The lab has scripts of the teacher the forecast does not run'),
            'outdated' => new external_value(PARAM_BOOL, 'Assessed by the checks the activity had before they were changed',
                VALUE_DEFAULT, false),
            'assessedat' => new external_value(PARAM_INT, 'When the snapshot was assessed, 0 if unknown'),
        ], 'Forecast from the facts, null without one or when the user may not see grades', VALUE_DEFAULT, null, NULL_ALLOWED);
    }

    /**
     * Unix time of an ISO 8601 time of the manager.
     *
     * @param mixed $value time
     * @return int 0 if it is not a time
     */
    protected static function timestamp(mixed $value): int {
        $time = is_string($value) ? strtotime($value) : false;
        return $time === false ? 0 : $time;
    }

    /**
     * Cache key of the forecasts of an activity under a grading.
     *
     * @param int $instanceid gns3lab id
     * @param array $grading grading of the activity
     * @return string
     */
    protected static function key(int $instanceid, array $grading): string {
        $baseurl = rtrim(trim((string) get_config('gns3lab', 'managerurl')), '/');
        return 'f' . sha1($baseurl . "\n" . $instanceid . "\n" . json_encode($grading));
    }
}
