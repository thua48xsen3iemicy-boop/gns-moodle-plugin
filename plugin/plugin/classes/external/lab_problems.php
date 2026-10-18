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

use core_external\external_multiple_structure;
use core_external\external_single_structure;
use core_external\external_value;
use mod_gns3lab\local\manager_exception;

/**
 * Problems the lab manager found in a lab definition (422 lab_invalid), for the editor.
 *
 * @package    mod_gns3lab
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
class lab_problems {
    /**
     * Structure of the list.
     *
     * @return external_multiple_structure
     */
    public static function structure(): external_multiple_structure {
        return new external_multiple_structure(new external_single_structure([
            'path' => new external_value(PARAM_RAW, 'Place in the definition, such as nodes[2].adapters'),
            'code' => new external_value(PARAM_ALPHANUMEXT, 'Problem code of the lab manager'),
            'message' => new external_value(PARAM_RAW, 'Message of the lab manager, for debugging'),
        ]));
    }

    /**
     * Problems from a lab_invalid error; other errors are thrown again.
     *
     * @param manager_exception $e error of the lab manager
     * @return array
     */
    public static function from_exception(manager_exception $e): array {
        if ($e->remotecode !== 'lab_invalid') {
            throw $e;
        }
        $problems = [];
        foreach (is_array($e->details->problems ?? null) ? $e->details->problems : [] as $p) {
            $problems[] = [
                'path' => \core_text::substr((string) ($p->path ?? ''), 0, 255),
                'code' => preg_replace('/[^a-z0-9_]/', '', strtolower((string) ($p->code ?? ''))) ?: 'invalid',
                'message' => \core_text::substr((string) ($p->message ?? ''), 0, 500),
            ];
        }
        return $problems ?: [['path' => '', 'code' => 'invalid', 'message' => '']];
    }
}
