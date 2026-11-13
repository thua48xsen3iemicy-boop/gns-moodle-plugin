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

/**
 * Upgrade steps of mod_gns3lab.
 *
 * @package    mod_gns3lab
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

/**
 * Runs the upgrade steps from an older version.
 *
 * @param int $oldversion version installed before
 * @return bool
 */
function xmldb_gns3lab_upgrade($oldversion): bool {
    global $DB;
    $dbman = $DB->get_manager();

    if ($oldversion < 2026101900) {
        // The site lab library: labs, their versions and the version chosen by an activity.
        $table = new xmldb_table('gns3lab_labs');
        $table->add_field('id', XMLDB_TYPE_INTEGER, '10', null, XMLDB_NOTNULL, XMLDB_SEQUENCE, null);
        $table->add_field('name', XMLDB_TYPE_CHAR, '255', null, XMLDB_NOTNULL, null, null);
        $table->add_field('description', XMLDB_TYPE_TEXT, null, null, null, null, null);
        $table->add_field('descriptionformat', XMLDB_TYPE_INTEGER, '4', null, XMLDB_NOTNULL, null, '0');
        $table->add_field('usercreated', XMLDB_TYPE_INTEGER, '10', null, XMLDB_NOTNULL, null, '0');
        $table->add_field('usermodified', XMLDB_TYPE_INTEGER, '10', null, XMLDB_NOTNULL, null, '0');
        $table->add_field('timecreated', XMLDB_TYPE_INTEGER, '10', null, XMLDB_NOTNULL, null, '0');
        $table->add_field('timemodified', XMLDB_TYPE_INTEGER, '10', null, XMLDB_NOTNULL, null, '0');
        $table->add_key('primary', XMLDB_KEY_PRIMARY, ['id']);
        $table->add_key('usercreated', XMLDB_KEY_FOREIGN, ['usercreated'], 'user', ['id']);
        $table->add_key('usermodified', XMLDB_KEY_FOREIGN, ['usermodified'], 'user', ['id']);
        if (!$dbman->table_exists($table)) {
            $dbman->create_table($table);
        }

        $table = new xmldb_table('gns3lab_lab_versions');
        $table->add_field('id', XMLDB_TYPE_INTEGER, '10', null, XMLDB_NOTNULL, XMLDB_SEQUENCE, null);
        $table->add_field('labid', XMLDB_TYPE_INTEGER, '10', null, XMLDB_NOTNULL, null, null);
        $table->add_field('version', XMLDB_TYPE_INTEGER, '10', null, XMLDB_NOTNULL, null, null);
        $table->add_field('status', XMLDB_TYPE_CHAR, '10', null, XMLDB_NOTNULL, null, 'draft');
        $table->add_field('definition', XMLDB_TYPE_TEXT, null, null, XMLDB_NOTNULL, null, null);
        $table->add_field('labdef', XMLDB_TYPE_CHAR, '80', null, null, null, null);
        $table->add_field('memorymb', XMLDB_TYPE_INTEGER, '10', null, XMLDB_NOTNULL, null, '0');
        $table->add_field('cores', XMLDB_TYPE_INTEGER, '10', null, XMLDB_NOTNULL, null, '0');
        $table->add_field('usermodified', XMLDB_TYPE_INTEGER, '10', null, XMLDB_NOTNULL, null, '0');
        $table->add_field('timecreated', XMLDB_TYPE_INTEGER, '10', null, XMLDB_NOTNULL, null, '0');
        $table->add_field('timemodified', XMLDB_TYPE_INTEGER, '10', null, XMLDB_NOTNULL, null, '0');
        $table->add_field('timepublished', XMLDB_TYPE_INTEGER, '10', null, XMLDB_NOTNULL, null, '0');
        $table->add_key('primary', XMLDB_KEY_PRIMARY, ['id']);
        $table->add_key('labid', XMLDB_KEY_FOREIGN, ['labid'], 'gns3lab_labs', ['id']);
        $table->add_key('usermodified', XMLDB_KEY_FOREIGN, ['usermodified'], 'user', ['id']);
        $table->add_index('labid_version', XMLDB_INDEX_UNIQUE, ['labid', 'version']);
        if (!$dbman->table_exists($table)) {
            $dbman->create_table($table);
        }

        $table = new xmldb_table('gns3lab');
        $field = new xmldb_field('labversionid', XMLDB_TYPE_INTEGER, '10', null, XMLDB_NOTNULL, null, '0', 'maxduration');
        if (!$dbman->field_exists($table, $field)) {
            $dbman->add_field($table, $field);
        }
        $key = new xmldb_key('labversionid', XMLDB_KEY_FOREIGN, ['labversionid'], 'gns3lab_lab_versions', ['id']);
        $dbman->add_key($table, $key);

        \mod_gns3lab\local\lab_library::create_author_role();

        upgrade_mod_savepoint(true, 2026101900, 'gns3lab');
    }

    if ($oldversion < 2026102700) {
        // What the scheme shows a student (live, seen, fog) and what a student has uncovered in the last two modes.
        $table = new xmldb_table('gns3lab');
        $field = new xmldb_field('factsview', XMLDB_TYPE_CHAR, '10', null, XMLDB_NOTNULL, null, 'live', 'labversionid');
        if (!$dbman->field_exists($table, $field)) {
            $dbman->add_field($table, $field);
        }
        $field = new xmldb_field('fogstart', XMLDB_TYPE_TEXT, null, null, null, null, null, 'factsview');
        if (!$dbman->field_exists($table, $field)) {
            $dbman->add_field($table, $field);
        }

        $table = new xmldb_table('gns3lab_reveal');
        $table->add_field('id', XMLDB_TYPE_INTEGER, '10', null, XMLDB_NOTNULL, XMLDB_SEQUENCE, null);
        $table->add_field('gns3labid', XMLDB_TYPE_INTEGER, '10', null, XMLDB_NOTNULL, null, null);
        $table->add_field('userid', XMLDB_TYPE_INTEGER, '10', null, XMLDB_NOTNULL, null, null);
        $table->add_field('nodeid', XMLDB_TYPE_CHAR, '64', null, XMLDB_NOTNULL, null);
        $table->add_field('kind', XMLDB_TYPE_CHAR, '10', null, XMLDB_NOTNULL, null, null);
        $table->add_field('item', XMLDB_TYPE_CHAR, '100', null, XMLDB_NOTNULL, null);
        $table->add_field('timecreated', XMLDB_TYPE_INTEGER, '10', null, XMLDB_NOTNULL, null, '0');
        $table->add_key('primary', XMLDB_KEY_PRIMARY, ['id']);
        $table->add_key('gns3labid', XMLDB_KEY_FOREIGN, ['gns3labid'], 'gns3lab', ['id']);
        $table->add_key('userid', XMLDB_KEY_FOREIGN, ['userid'], 'user', ['id']);
        $table->add_index('uniqueitem', XMLDB_INDEX_UNIQUE, ['gns3labid', 'userid', 'nodeid', 'kind', 'item']);
        if (!$dbman->table_exists($table)) {
            $dbman->create_table($table);
        }

        upgrade_mod_savepoint(true, 2026102700, 'gns3lab');
    }

    if ($oldversion < 2026103100) {
        // The one-seat rule (docs/seat.md): how an activity limits parallel work under one account, and who holds the
        // seat of a student now. Existing activities get the default, the strictest mode (one browser page).
        $table = new xmldb_table('gns3lab');
        $field = new xmldb_field('seatmode', XMLDB_TYPE_CHAR, '10', null, XMLDB_NOTNULL, null, 'tab', 'fogstart');
        if (!$dbman->field_exists($table, $field)) {
            $dbman->add_field($table, $field);
        }

        $table = new xmldb_table('gns3lab_seat');
        $table->add_field('id', XMLDB_TYPE_INTEGER, '10', null, XMLDB_NOTNULL, XMLDB_SEQUENCE, null);
        $table->add_field('gns3labid', XMLDB_TYPE_INTEGER, '10', null, XMLDB_NOTNULL, null, null);
        $table->add_field('userid', XMLDB_TYPE_INTEGER, '10', null, XMLDB_NOTNULL, null, null);
        $table->add_field('sessionkey', XMLDB_TYPE_CHAR, '64', null, XMLDB_NOTNULL, null, null);
        $table->add_field('tabid', XMLDB_TYPE_CHAR, '64', null, XMLDB_NOTNULL, null);
        $table->add_field('timeclaimed', XMLDB_TYPE_INTEGER, '10', null, XMLDB_NOTNULL, null, '0');
        $table->add_field('timemodified', XMLDB_TYPE_INTEGER, '10', null, XMLDB_NOTNULL, null, '0');
        $table->add_key('primary', XMLDB_KEY_PRIMARY, ['id']);
        $table->add_key('gns3labid', XMLDB_KEY_FOREIGN, ['gns3labid'], 'gns3lab', ['id']);
        $table->add_key('userid', XMLDB_KEY_FOREIGN, ['userid'], 'user', ['id']);
        $table->add_index('oneseat', XMLDB_INDEX_UNIQUE, ['gns3labid', 'userid']);
        if (!$dbman->table_exists($table)) {
            $dbman->create_table($table);
        }

        upgrade_mod_savepoint(true, 2026103100, 'gns3lab');
    }

    if ($oldversion < 2026110800) {
        // Auto-checks (docs/autochecks.md): the settings of an activity, its grade and the runs of the students.
        $table = new xmldb_table('gns3lab');
        $fields = [
            new xmldb_field('grade', XMLDB_TYPE_INTEGER, '10', null, XMLDB_NOTNULL, null, '0', 'seatmode'),
            new xmldb_field('checksenabled', XMLDB_TYPE_INTEGER, '1', null, XMLDB_NOTNULL, null, '0', 'grade'),
            new xmldb_field('checksprofile', XMLDB_TYPE_CHAR, '10', null, XMLDB_NOTNULL, null, 'standard', 'checksenabled'),
            new xmldb_field('checksthresholds', XMLDB_TYPE_TEXT, null, null, null, null, null, 'checksprofile'),
            new xmldb_field('checksfailmark', XMLDB_TYPE_INTEGER, '3', null, XMLDB_NOTNULL, null, '2', 'checksthresholds'),
            new xmldb_field('checksoverrides', XMLDB_TYPE_TEXT, null, null, null, null, null, 'checksfailmark'),
            new xmldb_field('checksattempts', XMLDB_TYPE_INTEGER, '5', null, XMLDB_NOTNULL, null, '0', 'checksoverrides'),
            new xmldb_field('checksreport', XMLDB_TYPE_CHAR, '10', null, XMLDB_NOTNULL, null, 'full', 'checksattempts'),
            new xmldb_field('checksreportdate', XMLDB_TYPE_INTEGER, '10', null, XMLDB_NOTNULL, null, '0', 'checksreport'),
        ];
        foreach ($fields as $field) {
            if (!$dbman->field_exists($table, $field)) {
                $dbman->add_field($table, $field);
            }
        }

        $table = new xmldb_table('gns3lab_check_runs');
        $table->add_field('id', XMLDB_TYPE_INTEGER, '10', null, XMLDB_NOTNULL, XMLDB_SEQUENCE, null);
        $table->add_field('gns3labid', XMLDB_TYPE_INTEGER, '10', null, XMLDB_NOTNULL, null, null);
        $table->add_field('userid', XMLDB_TYPE_INTEGER, '10', null, XMLDB_NOTNULL, null, null);
        $table->add_field('sessionid', XMLDB_TYPE_CHAR, '64', null, XMLDB_NOTNULL, null, null);
        $table->add_field('runid', XMLDB_TYPE_CHAR, '64', null, XMLDB_NOTNULL, null, null);
        $table->add_field('requestedby', XMLDB_TYPE_INTEGER, '10', null, XMLDB_NOTNULL, null, '0');
        $table->add_field('status', XMLDB_TYPE_CHAR, '10', null, XMLDB_NOTNULL, null, 'queued');
        $table->add_field('outcome', XMLDB_TYPE_CHAR, '12', null, XMLDB_NOTNULL, null);
        $table->add_field('mark', XMLDB_TYPE_INTEGER, '5', null, null, null, null);
        $table->add_field('topmark', XMLDB_TYPE_INTEGER, '5', null, XMLDB_NOTNULL, null, '0');
        $table->add_field('failmark', XMLDB_TYPE_INTEGER, '5', null, XMLDB_NOTNULL, null, '0');
        $table->add_field('critical', XMLDB_TYPE_INTEGER, '5', null, XMLDB_NOTNULL, null, '0');
        $table->add_field('major', XMLDB_TYPE_INTEGER, '5', null, XMLDB_NOTNULL, null, '0');
        $table->add_field('minor', XMLDB_TYPE_INTEGER, '5', null, XMLDB_NOTNULL, null, '0');
        $table->add_field('note', XMLDB_TYPE_INTEGER, '5', null, XMLDB_NOTNULL, null, '0');
        $table->add_field('errorcode', XMLDB_TYPE_CHAR, '32', null, XMLDB_NOTNULL, null);
        $table->add_field('timecreated', XMLDB_TYPE_INTEGER, '10', null, XMLDB_NOTNULL, null, '0');
        $table->add_field('timemodified', XMLDB_TYPE_INTEGER, '10', null, XMLDB_NOTNULL, null, '0');
        $table->add_field('timefinished', XMLDB_TYPE_INTEGER, '10', null, XMLDB_NOTNULL, null, '0');
        $table->add_key('primary', XMLDB_KEY_PRIMARY, ['id']);
        $table->add_key('gns3labid', XMLDB_KEY_FOREIGN, ['gns3labid'], 'gns3lab', ['id']);
        $table->add_key('userid', XMLDB_KEY_FOREIGN, ['userid'], 'user', ['id']);
        $table->add_index('labrun', XMLDB_INDEX_UNIQUE, ['gns3labid', 'runid']);
        $table->add_index('labuser', XMLDB_INDEX_NOTUNIQUE, ['gns3labid', 'userid']);
        $table->add_index('status', XMLDB_INDEX_NOTUNIQUE, ['status']);
        if (!$dbman->table_exists($table)) {
            $dbman->create_table($table);
        }

        upgrade_mod_savepoint(true, 2026110800, 'gns3lab');
    }

    if ($oldversion < 2026110903) {
        // Requests to delete student work on the lab manager (manager CONTRACTS 2.10).
        $table = new xmldb_table('gns3lab_purges');
        $table->add_field('id', XMLDB_TYPE_INTEGER, '10', null, XMLDB_NOTNULL, XMLDB_SEQUENCE, null);
        $table->add_field('instanceid', XMLDB_TYPE_INTEGER, '10', null, XMLDB_NOTNULL, null, '0');
        $table->add_field('userid', XMLDB_TYPE_INTEGER, '10', null, XMLDB_NOTNULL, null, '0');
        $table->add_field('librarylabid', XMLDB_TYPE_INTEGER, '10', null, XMLDB_NOTNULL, null, '0');
        $table->add_field('attempts', XMLDB_TYPE_INTEGER, '10', null, XMLDB_NOTNULL, null, '0');
        $table->add_field('nextattempt', XMLDB_TYPE_INTEGER, '10', null, XMLDB_NOTNULL, null, '0');
        $table->add_field('lasterror', XMLDB_TYPE_CHAR, '64', null, XMLDB_NOTNULL, null);
        $table->add_field('timecreated', XMLDB_TYPE_INTEGER, '10', null, XMLDB_NOTNULL, null, '0');
        $table->add_key('primary', XMLDB_KEY_PRIMARY, ['id']);
        $table->add_index('request', XMLDB_INDEX_UNIQUE, ['instanceid', 'userid', 'librarylabid']);
        $table->add_index('nextattempt', XMLDB_INDEX_NOTUNIQUE, ['nextattempt']);
        if (!$dbman->table_exists($table)) {
            $dbman->create_table($table);
        }

        upgrade_mod_savepoint(true, 2026110903, 'gns3lab');
    }

    if ($oldversion < 2026111200) {
        // Idle stop (docs/idle-stop.md): the beat of a seat at which the task last looked at its lab.
        $table = new xmldb_table('gns3lab_seat');
        $field = new xmldb_field('timeidlechecked', XMLDB_TYPE_INTEGER, '10', null, XMLDB_NOTNULL, null, '0', 'timemodified');
        if (!$dbman->field_exists($table, $field)) {
            $dbman->add_field($table, $field);
        }

        upgrade_mod_savepoint(true, 2026111200, 'gns3lab');
    }

    if ($oldversion < 2026111301) {
        // No pasting into the consoles moved from the activity to the nodes of a library lab (docs/SPEC.md 5.1):
        // a site that ran a build of 2026111300 has the activity field, it goes.
        $table = new xmldb_table('gns3lab');
        $field = new xmldb_field('nopaste');
        if ($dbman->field_exists($table, $field)) {
            $dbman->drop_field($table, $field);
        }

        upgrade_mod_savepoint(true, 2026111301, 'gns3lab');
    }

    return true;
}
