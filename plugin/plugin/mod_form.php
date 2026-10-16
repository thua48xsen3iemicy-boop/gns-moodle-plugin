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
 * Activity settings form for mod_gns3lab.
 *
 * @package    mod_gns3lab
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

defined('MOODLE_INTERNAL') || die();

require_once($CFG->dirroot . '/course/moodleform_mod.php');

/**
 * Activity settings form.
 *
 * Until the lab manager is connected the reference lab is a free text field;
 * an empty value shows the bundled demo topology.
 */
class mod_gns3lab_mod_form extends moodleform_mod {
    /**
     * Defines the form fields.
     */
    public function definition(): void {
        $mform = $this->_form;

        $mform->addElement('header', 'general', get_string('general', 'form'));
        $mform->addElement('text', 'name', get_string('name'), ['size' => '64']);
        $mform->setType('name', PARAM_TEXT);
        $mform->addRule('name', null, 'required', null, 'client');
        $mform->addRule('name', get_string('maximumchars', '', 255), 'maxlength', 255, 'client');
        $this->standard_intro_elements();

        $mform->addElement('header', 'labsettings', get_string('labsettings', 'gns3lab'));
        $mform->setExpanded('labsettings');
        $mform->addElement('text', 'labref', get_string('labref', 'gns3lab'), ['size' => '48']);
        $mform->setType('labref', PARAM_TEXT);
        $mform->addRule('labref', get_string('maximumchars', '', 255), 'maxlength', 255, 'client');
        $mform->addHelpButton('labref', 'labref', 'gns3lab');

        $mform->addElement('text', 'maxduration', get_string('maxduration', 'gns3lab'), ['size' => '6']);
        $mform->setType('maxduration', PARAM_INT);
        $mform->setDefault('maxduration', 0);
        $mform->addHelpButton('maxduration', 'maxduration', 'gns3lab');

        $this->standard_coursemodule_elements();
        $this->add_action_buttons();
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
        if (isset($data['maxduration']) && (int) $data['maxduration'] < 0) {
            $errors['maxduration'] = get_string('maxdurationinvalid', 'gns3lab');
        }
        return $errors;
    }
}
