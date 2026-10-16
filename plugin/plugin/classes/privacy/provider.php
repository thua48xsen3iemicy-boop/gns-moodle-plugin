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

namespace mod_gns3lab\privacy;

use core_privacy\local\metadata\collection;
use core_privacy\local\request\approved_contextlist;
use core_privacy\local\request\approved_userlist;
use core_privacy\local\request\contextlist;
use core_privacy\local\request\userlist;

/**
 * Privacy provider for mod_gns3lab.
 *
 * The plugin stores no personal data in Moodle. The user id is sent to the external lab manager,
 * which runs the student's lab and keeps the student's saved project.
 *
 * @package    mod_gns3lab
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
class provider implements
    \core_privacy\local\metadata\provider,
    \core_privacy\local\request\core_userlist_provider,
    \core_privacy\local\request\plugin\provider {
    /**
     * Describes the data sent to the lab manager.
     *
     * @param collection $collection the collection to add to
     * @return collection
     */
    public static function get_metadata(collection $collection): collection {
        $collection->add_external_location_link('gns3labmanager', [
            'userid' => 'privacy:metadata:gns3labmanager:userid',
            'courseid' => 'privacy:metadata:gns3labmanager:courseid',
            'instanceid' => 'privacy:metadata:gns3labmanager:instanceid',
        ], 'privacy:metadata:gns3labmanager');
        return $collection;
    }

    /**
     * No data is stored in Moodle, so there are no contexts with user data.
     *
     * @param int $userid user id
     * @return contextlist
     */
    public static function get_contexts_for_userid(int $userid): contextlist {
        return new contextlist();
    }

    /**
     * No data is stored in Moodle, so there are no users to list.
     *
     * @param userlist $userlist the userlist to fill
     */
    public static function get_users_in_context(userlist $userlist) {
    }

    /**
     * Nothing to export from Moodle.
     *
     * @param approved_contextlist $contextlist approved contexts
     */
    public static function export_user_data(approved_contextlist $contextlist) {
    }

    /**
     * Nothing to delete in Moodle.
     *
     * @param \context $context the context
     */
    public static function delete_data_for_all_users_in_context(\context $context) {
    }

    /**
     * Nothing to delete in Moodle.
     *
     * @param approved_contextlist $contextlist approved contexts
     */
    public static function delete_data_for_user(approved_contextlist $contextlist) {
    }

    /**
     * Nothing to delete in Moodle.
     *
     * @param approved_userlist $userlist approved users
     */
    public static function delete_data_for_users(approved_userlist $userlist) {
    }
}
