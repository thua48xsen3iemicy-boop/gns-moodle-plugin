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
 * The lab is a published version from the site lab library or, for labs kept on the lab manager,
 * a reference lab (free text lab_ref); with neither the bundled demo topology is shown.
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
        $menu = [0 => get_string('labfrommanager', 'gns3lab')] + \mod_gns3lab\local\lab_library::published_menu();
        $mform->addElement('select', 'labversionid', get_string('librarylab', 'gns3lab'), $menu);
        $mform->setDefault('labversionid', 0);
        $mform->addHelpButton('labversionid', 'librarylab', 'gns3lab');
        if (has_capability('mod/gns3lab:managelabs', context_system::instance())) {
            $link = html_writer::link(new \core\url('/mod/gns3lab/library.php'), get_string('library', 'gns3lab'));
            $mform->addElement('static', 'librarylink', '', $link);
        }

        $mform->addElement('text', 'labref', get_string('labref', 'gns3lab'), ['size' => '48']);
        $mform->hideIf('labref', 'labversionid', 'neq', 0);
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
        $versionid = (int) ($data['labversionid'] ?? 0);
        if ($versionid && !array_key_exists($versionid, \mod_gns3lab\local\lab_library::published_menu())) {
            $errors['labversionid'] = get_string('labversionmissing', 'gns3lab');
        }
        if (isset($data['maxduration']) && (int) $data['maxduration'] < 0) {
            $errors['maxduration'] = get_string('maxdurationinvalid', 'gns3lab');
        }
        return $errors;
    }
}
