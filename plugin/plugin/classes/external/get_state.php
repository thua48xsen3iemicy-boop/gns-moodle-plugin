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
use mod_gns3lab\local\lab_access;
use mod_gns3lab\local\manager_client;
use mod_gns3lab\local\session_data;

/**
 * Current lab session of the user (or of an observed student) in an activity.
 *
 * The page polls it while the session is changing. The reference topology for the preview is not
 * part of the answer: the page gets it once, when it is rendered.
 *
 * @package    mod_gns3lab
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
class get_state extends external_api {
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
     * Returns the session state.
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

        $configured = manager_client::is_configured();
        $result = [
            'canuse' => false,
            'readonly' => false,
            'managerconfigured' => $configured,
            'labready' => $configured && trim($access->instance->labref) !== '',
            'resume' => false,
        ];
        $own = $userid === 0;
        if ($own && !has_capability('mod/gns3lab:use', $access->context)) {
            // Viewers only see the preview: there is no lab to ask the manager about.
            return $result;
        }
        [$userid, $readonly] = $access->resolve_user($userid);
        $result['canuse'] = !$readonly;
        $result['readonly'] = $readonly;
        if (!$result['labready']) {
            return $result;
        }
        $client = \core\di::get(manager_client::class);
        $result['resume'] = in_array('resume', $client->get_info()->features, true);
        $session = $client->find_session($userid, (int) $access->instance->id);
        if ($session !== null) {
            $result['session'] = session_data::export($session);
        }
        return $result;
    }

    /**
     * Result.
     *
     * @return external_single_structure
     */
    public static function execute_returns(): external_single_structure {
        return new external_single_structure([
            'canuse' => new external_value(PARAM_BOOL, 'The current user can start and stop this lab'),
            'readonly' => new external_value(PARAM_BOOL, 'Another user\'s lab: consoles are read only'),
            'managerconfigured' => new external_value(PARAM_BOOL, 'The lab manager is set up on this site'),
            'labready' => new external_value(PARAM_BOOL, 'The manager is set up and the activity has a reference lab'),
            'resume' => new external_value(PARAM_BOOL, 'The next start continues the saved project, else work is lost'),
            'session' => session_data::structure(VALUE_OPTIONAL),
        ]);
    }
}
