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
use mod_gns3lab\local\checks;
use mod_gns3lab\local\checks_lab;

/**
 * Asks the lab manager whether the auto-checks of the activity form fit the lab chosen there (CONTRACTS §2.9).
 *
 * @package    mod_gns3lab
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
class validate_checks extends external_api {
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
            'checks' => new external_value(PARAM_RAW, 'Document gns3lab.checks/1 as JSON'),
        ]);
    }

    /**
     * Checks the document.
     *
     * @param int $courseid course id
     * @param int $labversionid published version
     * @param string $labref reference lab
     * @param string $checks document as JSON
     * @return array
     */
    public static function execute(int $courseid, int $labversionid, string $labref, string $checks): array {
        $params = self::validate_parameters(self::execute_parameters(), [
            'courseid' => $courseid, 'labversionid' => $labversionid, 'labref' => $labref, 'checks' => $checks,
        ]);
        $context = \context_course::instance($params['courseid']);
        self::validate_context($context);
        require_capability('moodle/course:manageactivities', $context);

        $doc = checks::parse_document($params['checks']);
        if ($doc === null) {
            return ['valid' => false, 'checksdef' => '', 'error' => '',
                'problems' => [['path' => 'checks', 'code' => 'bad_type', 'message' => '']]];
        }
        $lab = checks_lab::lab_of($params['labversionid'], trim($params['labref']));
        if ($lab === null) {
            return ['valid' => false, 'checksdef' => '', 'problems' => [], 'error' => get_string('checksdefnolab', 'gns3lab')];
        }
        return checks_lab::validate($lab, $doc);
    }

    /**
     * Result.
     *
     * @return external_single_structure
     */
    public static function execute_returns(): external_single_structure {
        return new external_single_structure([
            'valid' => new external_value(PARAM_BOOL, 'The lab manager accepts the document'),
            'checksdef' => new external_value(PARAM_RAW, 'Hash of the document, empty unless valid'),
            'problems' => lab_problems::structure(),
            'error' => new external_value(PARAM_TEXT, 'Why the lab manager could not be asked, empty otherwise'),
        ]);
    }
}
