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

namespace mod_gns3lab\form;

use mod_gns3lab\local\lab_library;

/**
 * Name, description and tags of a library lab; the topology is edited in the lab editor.
 *
 * @package    mod_gns3lab
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
class lab_form extends \moodleform {
    /**
     * Defines the form fields.
     */
    protected function definition(): void {
        $mform = $this->_form;

        $mform->addElement('hidden', 'id');
        $mform->setType('id', PARAM_INT);

        $mform->addElement('text', 'name', get_string('labname', 'gns3lab'), ['size' => '64']);
        $mform->setType('name', PARAM_TEXT);
        $mform->addRule('name', null, 'required', null, 'client');
        $mform->addRule('name', get_string('maximumchars', '', 255), 'maxlength', 255, 'client');

        $mform->addElement('editor', 'description_editor', get_string('description'), ['rows' => 6], [
            'maxfiles' => 0,
            'context' => lab_library::context(),
        ]);
        $mform->setType('description_editor', PARAM_RAW);

        $mform->addElement('tags', 'tags', get_string('tags'), [
            'itemtype' => lab_library::TAG_ITEMTYPE,
            'component' => 'mod_gns3lab',
        ]);
        $mform->addHelpButton('tags', 'labtags', 'gns3lab');

        $this->add_action_buttons(true, empty($this->_customdata['isnew']) ? null : get_string('labcreate', 'gns3lab'));
    }

    /**
     * Validates the form data.
     *
     * @param array $data submitted data
     * @param array $files submitted files
     * @return array errors keyed by field name
     */
    public function validation($data, $files): array {
        $errors = parent::validation($data, $files);
        if (trim($data['name'] ?? '') === '') {
            $errors['name'] = get_string('required');
        }
        return $errors;
    }
}
