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
 * Library of interface functions for mod_gns3lab.
 *
 * @package    mod_gns3lab
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

/**
 * Declares which Moodle features the module supports.
 *
 * @param string $feature FEATURE_xx constant
 * @return mixed true/false for supported features, a value for FEATURE_MOD_PURPOSE, null if unknown
 */
function gns3lab_supports($feature) {
    return match ($feature) {
        FEATURE_MOD_INTRO => true,
        FEATURE_SHOW_DESCRIPTION => true,
        FEATURE_COMPLETION_TRACKS_VIEWS => true,
        FEATURE_GROUPS => true,
        FEATURE_GROUPINGS => true,
        FEATURE_GRADE_HAS_GRADE => false,
        FEATURE_GRADE_OUTCOMES => false,
        FEATURE_BACKUP_MOODLE2 => false,
        FEATURE_MOD_PURPOSE => MOD_PURPOSE_INTERACTIVECONTENT,
        default => null,
    };
}

/**
 * Creates a new activity instance.
 *
 * @param stdClass $data data from the module form
 * @param mod_gns3lab_mod_form|null $mform the form
 * @return int new instance id
 */
function gns3lab_add_instance(stdClass $data, $mform = null): int {
    global $DB;

    $data->timecreated = time();
    $data->timemodified = $data->timecreated;
    $data->labversionid = (int) ($data->labversionid ?? 0);
    $data->labref = $data->labversionid ? '' : trim($data->labref ?? '');
    $data->maxduration = (int) ($data->maxduration ?? 0);
    gns3lab_prepare_view_settings($data);
    return $DB->insert_record('gns3lab', $data);
}

/**
 * Updates an activity instance.
 *
 * @param stdClass $data data from the module form
 * @param mod_gns3lab_mod_form|null $mform the form
 * @return bool true on success
 */
function gns3lab_update_instance(stdClass $data, $mform = null): bool {
    global $DB;

    $data->id = $data->instance;
    $data->timemodified = time();
    $data->labversionid = (int) ($data->labversionid ?? 0);
    $data->labref = $data->labversionid ? '' : trim($data->labref ?? '');
    $data->maxduration = (int) ($data->maxduration ?? 0);
    gns3lab_prepare_view_settings($data);
    return $DB->update_record('gns3lab', $data);
}

/**
 * Deletes an activity instance.
 *
 * @param int $id instance id
 * @return bool true on success, false if the instance does not exist
 */
function gns3lab_delete_instance($id): bool {
    global $DB;

    if (!$DB->record_exists('gns3lab', ['id' => $id])) {
        return false;
    }
    \mod_gns3lab\local\reveal::delete((int) $id);
    $DB->delete_records('gns3lab', ['id' => $id]);
    return true;
}

/**
 * Normalises what the scheme shows a student before the activity is saved.
 *
 * The form sends the starting nodes of a fog as a list (a multiple choice) or as text; the table keeps a JSON list.
 * Starting nodes make sense only for a fog.
 *
 * @param stdClass $data data from the module form
 */
function gns3lab_prepare_view_settings(stdClass $data): void {
    $mode = \mod_gns3lab\local\reveal::mode((object) ['factsview' => $data->factsview ?? '']);
    $data->factsview = $mode;
    $names = $data->fogstart ?? '';
    $names = is_array($names) ? $names : \mod_gns3lab\local\reveal::parse_names((string) $names);
    $data->fogstart = $mode === \mod_gns3lab\local\reveal::FOG ? \mod_gns3lab\local\reveal::encode_names($names) : '';
}
