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
 * Data generator of mod_gns3lab.
 *
 * @package    mod_gns3lab
 * @category   test
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
class mod_gns3lab_generator extends testing_module_generator {
    /**
     * Creates an activity with a reference lab.
     *
     * @param array|stdClass|null $record data for the module
     * @param array|null $options general options for course module
     * @return stdClass record from the module-defined table with additional field cmid
     */
    public function create_instance($record = null, ?array $options = null) {
        $record = (array) $record + [
            'labref' => 'cisco-3x2',
            'maxduration' => 0,
        ];
        return parent::create_instance($record, (array) $options);
    }
}
