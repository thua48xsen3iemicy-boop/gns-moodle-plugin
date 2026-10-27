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
use mod_gns3lab\local\lab_definition;
use mod_gns3lab\local\lab_library;
use mod_gns3lab\local\manager_client;
use mod_gns3lab\local\manager_exception;

/**
 * Asks the lab manager to check a lab definition from the editor, without saving anything.
 *
 * @package    mod_gns3lab
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
class validate_lab extends external_api {
    /**
     * Parameters.
     *
     * @return external_function_parameters
     */
    public static function execute_parameters(): external_function_parameters {
        return new external_function_parameters([
            'labid' => new external_value(PARAM_INT, 'Lab id'),
            'definition' => new external_value(PARAM_RAW, 'Lab definition gns3lab.lab/1 or /2 as JSON'),
        ]);
    }

    /**
     * Checks the definition.
     *
     * @param int $labid lab id
     * @param string $definition definition as JSON
     * @return array
     */
    public static function execute(int $labid, string $definition): array {
        ['labid' => $labid, 'definition' => $definition] = self::validate_parameters(
            self::execute_parameters(),
            ['labid' => $labid, 'definition' => $definition]
        );
        $context = lab_library::context();
        self::validate_context($context);
        require_capability('mod/gns3lab:managelabs', $context);

        $lab = lab_library::get_lab($labid);
        $def = lab_definition::from_json($definition, $lab->name);
        try {
            $result = \core\di::get(manager_client::class)->validate_lab($def);
        } catch (manager_exception $e) {
            return [
                'valid' => false, 'memorymb' => 0, 'cores' => 0, 'problems' => lab_problems::from_exception($e),
                'warnings' => [],
            ];
        }
        return [
            'valid' => true,
            'memorymb' => max(0, (int) ($result->resources->memory_mb ?? 0)),
            'cores' => max(0, (int) ($result->resources->cores ?? 0)),
            'problems' => [],
            'warnings' => lab_problems::warnings($result),
        ];
    }

    /**
     * Result.
     *
     * @return external_single_structure
     */
    public static function execute_returns(): external_single_structure {
        return new external_single_structure([
            'valid' => new external_value(PARAM_BOOL, 'The lab manager accepts the definition'),
            'memorymb' => new external_value(PARAM_INT, 'Memory the lab needs, MB'),
            'cores' => new external_value(PARAM_INT, 'CPU cores the lab needs'),
            'problems' => lab_problems::structure(),
            'warnings' => lab_problems::structure(),
        ]);
    }
}
