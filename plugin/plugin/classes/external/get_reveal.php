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
use mod_gns3lab\local\lab_access;
use mod_gns3lab\local\reveal;

/**
 * The mask of what a student has uncovered on the scheme in the seen and fog modes (docs/lab-constructor/README.md §2.1).
 *
 * A student reads their own mask; a teacher who may observe the student reads the student's, for the switch
 * "as the student sees it". The mask holds identifiers and names, no values.
 *
 * @package    mod_gns3lab
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
class get_reveal extends external_api {
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
     * Returns the mask.
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
        [$userid] = $access->resolve_user($userid);

        return ['items' => reveal::load((int) $access->instance->id, $userid)];
    }

    /**
     * Result.
     *
     * @return external_single_structure
     */
    public static function execute_returns(): external_single_structure {
        return new external_single_structure([
            'items' => new external_multiple_structure(new external_single_structure([
                'nodeid' => new external_value(PARAM_ALPHANUMEXT, 'Node of the live project, empty for a link'),
                'kind' => new external_value(PARAM_ALPHA, 'node, known, iface, group or link'),
                'item' => new external_value(PARAM_RAW, 'Interface name, data group or link id, empty for node and known'),
            ]), 'What is uncovered'),
        ]);
    }
}
