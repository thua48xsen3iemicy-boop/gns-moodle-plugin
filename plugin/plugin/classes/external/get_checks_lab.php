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
use core_external\external_multiple_structure;
use core_external\external_single_structure;
use core_external\external_value;
use mod_gns3lab\local\checks;
use mod_gns3lab\local\checks_lab;

/**
 * The devices of the lab chosen in the activity form, for its checks block (docs/autochecks.md §12).
 *
 * @package    mod_gns3lab
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
class get_checks_lab extends external_api {
    /**
     * Parameters.
     *
     * @return external_function_parameters
     */
    public static function execute_parameters(): external_function_parameters {
        return new external_function_parameters([
            'courseid' => new external_value(PARAM_INT, 'Course of the activity'),
            'labversionid' => new external_value(PARAM_INT, 'Published library version, 0 for none', VALUE_DEFAULT, 0),
            'labref' => new external_value(PARAM_TEXT, 'Reference lab on the lab manager', VALUE_DEFAULT, ''),
        ]);
    }

    /**
     * The lab.
     *
     * @param int $courseid course id
     * @param int $labversionid published version
     * @param string $labref reference lab
     * @return array
     */
    public static function execute(int $courseid, int $labversionid = 0, string $labref = ''): array {
        ['courseid' => $courseid, 'labversionid' => $labversionid, 'labref' => $labref] = self::validate_parameters(
            self::execute_parameters(),
            ['courseid' => $courseid, 'labversionid' => $labversionid, 'labref' => $labref]
        );
        $context = \context_course::instance($courseid);
        self::validate_context($context);
        require_capability('moodle/course:manageactivities', $context);

        return self::export(checks_lab::context(checks_lab::lab_of($labversionid, trim($labref))));
    }

    /**
     * The context of checks_lab as this function returns it (also embedded in the form).
     *
     * @param array $ctx checks_lab::context()
     * @return array
     */
    public static function export(array $ctx): array {
        return [
            'found' => $ctx['found'],
            'catalog' => $ctx['catalog'],
            'nodes' => $ctx['nodes'],
            'links' => $ctx['links'],
            'segments' => json_encode($ctx['segments'], JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES),
            'labchecks' => checks::encode_document($ctx['labchecks']),
            'setup' => $ctx['setup'] ?? [],
        ];
    }

    /**
     * Result.
     *
     * @return external_single_structure
     */
    public static function execute_returns(): external_single_structure {
        return new external_single_structure([
            'found' => new external_value(PARAM_BOOL, 'The lab is known'),
            'catalog' => new external_value(PARAM_BOOL, 'A reference lab of the lab manager'),
            'nodes' => new external_multiple_structure(new external_single_structure([
                'name' => new external_value(PARAM_TEXT, 'Node name'),
                'image' => new external_value(PARAM_TEXT, 'Image title or node type'),
                'kind' => new external_value(PARAM_ALPHANUMEXT, 'Node kind'),
                'category' => new external_value(PARAM_ALPHANUMEXT, 'Image category'),
                'scripts' => new external_value(PARAM_BOOL, 'A teacher\'s script may run on it'),
            ])),
            'links' => new external_multiple_structure(new external_single_structure([
                'a' => new external_value(PARAM_TEXT, 'One end: node and port'),
                'b' => new external_value(PARAM_TEXT, 'Other end: node and port'),
            ])),
            'segments' => new external_value(PARAM_RAW, 'Segments of the lab as JSON'),
            'labchecks' => new external_value(PARAM_RAW, 'Checks the lab carries itself as JSON, empty for none'),
            'setup' => new external_multiple_structure(new external_single_structure([
                'id' => new external_value(PARAM_TEXT, 'Id of the setup step'),
                'title' => new external_value(PARAM_TEXT, 'What the step sets up'),
                'node' => new external_value(PARAM_TEXT, 'Its node'),
            ]), 'Setup steps of the lab without their code (docs/lab-setup/README.md §9)', VALUE_DEFAULT, []),
        ]);
    }
}
