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
use core_privacy\local\request\transform;
use core_privacy\local\request\userlist;
use core_privacy\local\request\writer;

/**
 * Privacy provider for mod_gns3lab.
 *
 * The user id is sent to the external lab manager, which runs the student's lab and keeps the student's
 * saved project. In Moodle the plugin only records who created and changed labs of the site lab library.
 * The labs are shared content: deleting a user's data hands their labs over to the main administrator.
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
        $collection->add_database_table('gns3lab_labs', [
            'name' => 'privacy:metadata:gns3lab_labs:name',
            'usercreated' => 'privacy:metadata:gns3lab_labs:usercreated',
            'usermodified' => 'privacy:metadata:gns3lab_labs:usermodified',
            'timecreated' => 'privacy:metadata:gns3lab_labs:timecreated',
            'timemodified' => 'privacy:metadata:gns3lab_labs:timemodified',
        ], 'privacy:metadata:gns3lab_labs');
        $collection->add_database_table('gns3lab_lab_versions', [
            'version' => 'privacy:metadata:gns3lab_lab_versions:version',
            'usermodified' => 'privacy:metadata:gns3lab_lab_versions:usermodified',
            'timemodified' => 'privacy:metadata:gns3lab_lab_versions:timemodified',
        ], 'privacy:metadata:gns3lab_lab_versions');
        return $collection;
    }

    /**
     * The system context, if the user created or changed a lab of the library.
     *
     * @param int $userid user id
     * @return contextlist
     */
    public static function get_contexts_for_userid(int $userid): contextlist {
        $contextlist = new contextlist();
        if (self::lab_ids($userid)) {
            $contextlist->add_system_context();
        }
        return $contextlist;
    }

    /**
     * Users who created or changed labs, in the system context.
     *
     * @param userlist $userlist the userlist to fill
     */
    public static function get_users_in_context(userlist $userlist) {
        if ($userlist->get_context()->contextlevel != CONTEXT_SYSTEM) {
            return;
        }
        $userlist->add_from_sql('usercreated', 'SELECT usercreated FROM {gns3lab_labs}', []);
        $userlist->add_from_sql('usermodified', 'SELECT usermodified FROM {gns3lab_labs}', []);
        $userlist->add_from_sql('usermodified', 'SELECT usermodified FROM {gns3lab_lab_versions}', []);
    }

    /**
     * Exports the labs the user created or changed.
     *
     * @param approved_contextlist $contextlist approved contexts
     */
    public static function export_user_data(approved_contextlist $contextlist) {
        global $DB;

        $userid = (int) $contextlist->get_user()->id;
        $system = \context_system::instance();
        if (!in_array($system->id, $contextlist->get_contextids()) || !($ids = self::lab_ids($userid))) {
            return;
        }
        $labs = [];
        foreach ($DB->get_records_list('gns3lab_labs', 'id', $ids, 'name') as $lab) {
            $labs[] = [
                'name' => format_string($lab->name, true, ['context' => $system]),
                'createdbyyou' => transform::yesno($lab->usercreated == $userid),
                'timecreated' => transform::datetime($lab->timecreated),
                'timemodified' => transform::datetime($lab->timemodified),
            ];
        }
        writer::with_context($system)->export_data([get_string('library', 'gns3lab')], (object) ['labs' => $labs]);
    }

    /**
     * Hands all labs over to the main administrator: the labs are shared and stay in the library.
     *
     * @param \context $context the context
     */
    public static function delete_data_for_all_users_in_context(\context $context) {
        global $DB;

        if ($context->contextlevel != CONTEXT_SYSTEM) {
            return;
        }
        $adminid = get_admin()->id;
        $DB->set_field('gns3lab_labs', 'usercreated', $adminid);
        $DB->set_field('gns3lab_labs', 'usermodified', $adminid);
        $DB->set_field('gns3lab_lab_versions', 'usermodified', $adminid);
    }

    /**
     * Hands the user's labs over to the main administrator.
     *
     * @param approved_contextlist $contextlist approved contexts
     */
    public static function delete_data_for_user(approved_contextlist $contextlist) {
        if (in_array(\context_system::instance()->id, $contextlist->get_contextids())) {
            self::hand_over([(int) $contextlist->get_user()->id]);
        }
    }

    /**
     * Hands the users' labs over to the main administrator.
     *
     * @param approved_userlist $userlist approved users
     */
    public static function delete_data_for_users(approved_userlist $userlist) {
        if ($userlist->get_context()->contextlevel == CONTEXT_SYSTEM) {
            self::hand_over(array_map('intval', $userlist->get_userids()));
        }
    }

    /**
     * Labs the user created or changed, or of which the user changed a version.
     *
     * @param int $userid user id
     * @return int[]
     */
    protected static function lab_ids(int $userid): array {
        global $DB;

        return array_map('intval', $DB->get_fieldset_sql(
            'SELECT id FROM {gns3lab_labs} WHERE usercreated = :u1 OR usermodified = :u2
             UNION
             SELECT labid FROM {gns3lab_lab_versions} WHERE usermodified = :u3',
            ['u1' => $userid, 'u2' => $userid, 'u3' => $userid]
        ));
    }

    /**
     * Replaces the users in the lab library by the main administrator.
     *
     * @param int[] $userids users
     */
    protected static function hand_over(array $userids): void {
        global $DB;

        if (!$userids) {
            return;
        }
        $adminid = get_admin()->id;
        [$insql, $params] = $DB->get_in_or_equal($userids, SQL_PARAMS_NAMED);
        $fields = ['gns3lab_labs' => ['usercreated', 'usermodified'], 'gns3lab_lab_versions' => ['usermodified']];
        foreach ($fields as $table => $tablefields) {
            foreach ($tablefields as $field) {
                $DB->set_field_select($table, $field, $adminid, "{$field} {$insql}", $params);
            }
        }
    }
}
