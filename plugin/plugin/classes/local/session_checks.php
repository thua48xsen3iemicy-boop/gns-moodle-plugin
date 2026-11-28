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
 * Keeps the auto-checks of a live lab in step with its activity (docs/autochecks.md §12, CONTRACTS §2.9).
 *
 * The lab gets the activity's checks when it starts and on every edit (task push_checks_definition). Before a check the
 * plugin looks once more: the session names the description it is checked by (checks_def, checks_source), and one that
 * differs from the activity's is replaced. A lab that runs an earlier version of the lab, where the activity's checks
 * do not fit, keeps its own: the run is shown as one by earlier checks.
 *
 * @package    mod_gns3lab
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
class session_checks {
    /**
     * Whether a session is checked by another description than its activity wants.
     *
     * A session of a manager without activity-checks names none: nothing to compare.
     *
     * @param \stdClass $instance gns3lab record
     * @param \stdClass $session session from the lab manager
     * @return bool
     */
    public static function differs(\stdClass $instance, \stdClass $session): bool {
        if (!property_exists($session, 'checks_source')) {
            return false;
        }
        $own = checks::activity_document($instance) !== null;
        if (!$own) {
            return $session->checks_source === 'activity';
        }
        return $session->checks_source !== 'activity' || !empty($session->checks_error)
            || (string) ($session->checks_def ?? '') !== (string) ($instance->checksdefhash ?? '');
    }

    /**
     * Hands the activity's checks to a live session when it is checked by another description.
     *
     * @param \stdClass $instance gns3lab record
     * @param \stdClass $session session from the lab manager; checks_def, checks_source and checks_error follow
     * @param manager_client $client manager client
     * @return bool the session has the activity's checks now (or had them)
     */
    public static function sync(\stdClass $instance, \stdClass $session, manager_client $client): bool {
        if (!self::differs($instance, $session)) {
            return true;
        }
        try {
            $result = $client->put_checks_definition($session->session_id, checks::activity_document($instance));
        } catch (manager_exception $e) {
            if (in_array($e->remotecode, ['checks_invalid', 'session_not_running'], true)) {
                return false;
            }
            throw $e;
        }
        $session->checks_def = $result->checks_def ?? null;
        $session->checks_source = $result->checks_source ?? null;
        unset($session->checks_error);
        if ($session->status === 'running' && !session_data::has_feature($session, 'checks')
                && session_data::has_feature($session, 'facts')) {
            // The manager gives the feature back once the checks fit (CONTRACTS §2.3).
            $session->features = array_merge(session_data::features($session), ['checks']);
        }
        return true;
    }
}
