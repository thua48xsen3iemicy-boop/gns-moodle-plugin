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

        $mform->addElement('select', 'factsview', get_string('factsview', 'gns3lab'), [
            \mod_gns3lab\local\reveal::LIVE => get_string('factsview_live', 'gns3lab'),
            \mod_gns3lab\local\reveal::SEEN => get_string('factsview_seen', 'gns3lab'),
            \mod_gns3lab\local\reveal::FOG => get_string('factsview_fog', 'gns3lab'),
        ]);
        $mform->setDefault('factsview', \mod_gns3lab\local\reveal::LIVE);
        $mform->addHelpButton('factsview', 'factsview', 'gns3lab');

        // The names of the nodes are known only for a lab that is already chosen: a new activity gets a text field.
        $names = empty($this->current->instance) ? null : \mod_gns3lab\local\reveal::lab_node_names($this->current);
        if ($names) {
            $select = $mform->addElement('select', 'fogstart', get_string('fogstart', 'gns3lab'), array_combine($names, $names));
            $select->setMultiple(true);
        } else {
            $mform->addElement('text', 'fogstart', get_string('fogstart', 'gns3lab'), ['size' => '48']);
            $mform->setType('fogstart', PARAM_TEXT);
        }
        $mform->hideIf('fogstart', 'factsview', 'neq', \mod_gns3lab\local\reveal::FOG);
        $mform->addHelpButton('fogstart', 'fogstart', 'gns3lab');

        // Parallel work under one account (docs/seat.md): by default a lab is driven from one browser page at a time.
        $mform->addElement('select', 'seatmode', get_string('seatmode', 'gns3lab'), [
            \mod_gns3lab\local\seat::TAB => get_string('seatmode_tab', 'gns3lab'),
            \mod_gns3lab\local\seat::LOGIN => get_string('seatmode_login', 'gns3lab'),
            \mod_gns3lab\local\seat::OFF => get_string('seatmode_off', 'gns3lab'),
        ]);
        $mform->setDefault('seatmode', \mod_gns3lab\local\seat::TAB);
        $mform->addHelpButton('seatmode', 'seatmode', 'gns3lab');

        if (self::manager_offers_checks()) {
            $this->checks_elements();
        }

        $this->standard_coursemodule_elements();
        // A new activity has no grade: only the auto-checks give one (docs/autochecks.md), and not every lab has them.
        if ($mform->elementExists('grade') && empty($this->current->instance)) {
            $mform->setDefault('grade', 0);
        }
        $this->add_action_buttons();
    }

    /**
     * Whether the lab manager offers auto-checks (CONTRACTS §2.9): GET /api/v1/info, cached by the client.
     *
     * @return bool
     */
    protected static function manager_offers_checks(): bool {
        if (!\mod_gns3lab\local\manager_client::is_configured()) {
            return false;
        }
        try {
            return \core\di::get(\mod_gns3lab\local\manager_client::class)->has_feature('checks');
        } catch (\mod_gns3lab\local\manager_exception $e) {
            return false;
        }
    }

    /**
     * The auto-check settings (docs/autochecks.md): strictness, thresholds, attempts, class overrides and the report.
     */
    protected function checks_elements(): void {
        $mform = $this->_form;
        $mform->addElement('header', 'checkssettings', get_string('checkssettings', 'gns3lab'));
        $mform->addElement('advcheckbox', 'checksenabled', get_string('checksenabled', 'gns3lab'));
        $mform->addHelpButton('checksenabled', 'checksenabled', 'gns3lab');

        $profiles = [];
        foreach (\mod_gns3lab\local\checks::PROFILES as $profile) {
            $profiles[$profile] = get_string('checksprofile_' . $profile, 'gns3lab');
        }
        $mform->addElement('select', 'checksprofile', get_string('checksprofile', 'gns3lab'), $profiles);
        $mform->setDefault('checksprofile', \mod_gns3lab\local\checks::DEFAULT_PROFILE);
        $mform->addHelpButton('checksprofile', 'checksprofile', 'gns3lab');
        $mform->hideIf('checksprofile', 'checksenabled');

        $defaults = \mod_gns3lab\local\checks::DEFAULT_THRESHOLDS;
        $labels = [
            'mark' => get_string('checksthrmark', 'gns3lab'),
            'critical' => get_string('checksthrcritical', 'gns3lab'),
            'major' => get_string('checksthrmajor', 'gns3lab'),
            'minor' => get_string('checksthrminor', 'gns3lab'),
        ];
        for ($i = 0; $i < \mod_gns3lab\local\checks::THRESHOLD_ROWS; $i++) {
            $group = [];
            foreach ($labels as $field => $label) {
                $name = "checksthr{$i}{$field}";
                $group[] = $mform->createElement('static', "{$name}label", '', $label);
                $group[] = $mform->createElement('text', $name, $label, ['size' => 3, 'inputmode' => 'numeric']);
                $mform->setType($name, PARAM_RAW_TRIMMED);
                $value = $defaults[$i][$field] ?? null;
                $mform->setDefault($name, $value === null ? '' : (string) $value);
            }
            $mform->addGroup($group, "checksthr{$i}", get_string('checksthreshold', 'gns3lab', $i + 1), ' ', false);
            $mform->hideIf("checksthr{$i}", 'checksenabled');
            if ($i === 0) {
                $mform->addHelpButton("checksthr{$i}", 'checksthreshold', 'gns3lab');
            }
        }

        $mform->addElement('text', 'checksfailmark', get_string('checksfailmark', 'gns3lab'), ['size' => 3]);
        $mform->setType('checksfailmark', PARAM_INT);
        $mform->setDefault('checksfailmark', \mod_gns3lab\local\checks::DEFAULT_FAIL_MARK);
        $mform->addHelpButton('checksfailmark', 'checksfailmark', 'gns3lab');
        $mform->hideIf('checksfailmark', 'checksenabled');

        $mform->addElement('text', 'checksattempts', get_string('checksattempts', 'gns3lab'), ['size' => 3]);
        $mform->setType('checksattempts', PARAM_INT);
        $mform->setDefault('checksattempts', 0);
        $mform->addHelpButton('checksattempts', 'checksattempts', 'gns3lab');
        $mform->hideIf('checksattempts', 'checksenabled');

        $mform->addElement(
            'textarea',
            'checksoverridestext',
            get_string('checksoverrides', 'gns3lab'),
            ['rows' => 4, 'cols' => 48, 'spellcheck' => 'false']
        );
        $mform->setType('checksoverridestext', PARAM_RAW_TRIMMED);
        $mform->addHelpButton('checksoverridestext', 'checksoverrides', 'gns3lab');
        $mform->hideIf('checksoverridestext', 'checksenabled');

        $mform->addElement('select', 'checksreport', get_string('checksreport', 'gns3lab'), [
            \mod_gns3lab\local\checks::REPORT_FULL => get_string('checksreport_full', 'gns3lab'),
            \mod_gns3lab\local\checks::REPORT_COUNTS => get_string('checksreport_counts', 'gns3lab'),
            \mod_gns3lab\local\checks::REPORT_AFTER => get_string('checksreport_after', 'gns3lab'),
        ]);
        $mform->setDefault('checksreport', \mod_gns3lab\local\checks::REPORT_FULL);
        $mform->addHelpButton('checksreport', 'checksreport', 'gns3lab');
        $mform->hideIf('checksreport', 'checksenabled');

        $mform->addElement('date_time_selector', 'checksreportdate', get_string('checksreportdate', 'gns3lab'));
        $mform->hideIf('checksreportdate', 'checksenabled');
        $mform->hideIf('checksreportdate', 'checksreport', 'neq', \mod_gns3lab\local\checks::REPORT_AFTER);
    }

    /**
     * Shows the starting nodes of a fog the way the field wants them: a list for a multiple choice, text otherwise.
     *
     * @param array $defaultvalues values of the saved activity
     */
    public function data_preprocessing(&$defaultvalues): void {
        parent::data_preprocessing($defaultvalues);
        $names = \mod_gns3lab\local\reveal::parse_names($defaultvalues['fogstart'] ?? '');
        $field = $this->_form->getElement('fogstart');
        $defaultvalues['fogstart'] = $field->getType() === 'select' ? $names : implode(', ', $names);
        if (!empty($defaultvalues['instance']) && $this->_form->elementExists('checksenabled')) {
            $instance = (object) $defaultvalues;
            $rows = \mod_gns3lab\local\checks::thresholds($instance);
            for ($i = 0; $i < \mod_gns3lab\local\checks::THRESHOLD_ROWS; $i++) {
                foreach (['mark', 'critical', 'major', 'minor'] as $key) {
                    $value = $rows[$i][$key] ?? null;
                    $defaultvalues["checksthr{$i}{$key}"] = $value === null ? '' : (string) $value;
                }
            }
            $defaultvalues['checksoverridestext'] = \mod_gns3lab\local\checks::format_overrides(
                \mod_gns3lab\local\checks::overrides($instance)
            );
            if (empty($defaultvalues['checksreportdate'])) {
                unset($defaultvalues['checksreportdate']);
            }
        }
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
        if (!empty($data['checksenabled'])) {
            $errors += $this->checks_errors($data);
        }
        if (($data['factsview'] ?? '') === \mod_gns3lab\local\reveal::FOG) {
            $error = $this->fogstart_error($data);
            if ($error !== '') {
                $errors['fogstart'] = $error;
            }
        }
        return $errors;
    }

    /**
     * Checks the auto-check settings by the rules of the lab manager (CONTRACTS §2.9), so it never refuses them.
     *
     * @param array $data submitted data
     * @return array errors keyed by field name
     */
    protected function checks_errors(array $data): array {
        $errors = [];
        $rows = [];
        for ($i = 0; $i < \mod_gns3lab\local\checks::THRESHOLD_ROWS; $i++) {
            $row = [];
            foreach (['mark', 'critical', 'major', 'minor'] as $key) {
                $row[$key] = trim((string) ($data["checksthr{$i}{$key}"] ?? ''));
            }
            if ($row['mark'] === '' && ($row['critical'] !== '' || $row['major'] !== '' || $row['minor'] !== '')) {
                $errors["checksthr{$i}"] = get_string('checksthresholdsmark', 'gns3lab');
            }
            $rows[] = $row;
        }
        $failmark = (int) ($data['checksfailmark'] ?? 0);
        if ($failmark < 0 || $failmark > \mod_gns3lab\local\checks::MAX_MARK) {
            $errors['checksfailmark'] = get_string('checksfailmarkinvalid', 'gns3lab');
        }
        $error = \mod_gns3lab\local\checks::thresholds_error(\mod_gns3lab\local\checks::clean_thresholds($rows), $failmark);
        if ($error !== '' && !$errors) {
            $errors[$error === 'checksfailmarkhigh' ? 'checksfailmark' : 'checksthr0'] = get_string($error, 'gns3lab');
        }
        if ((int) ($data['checksattempts'] ?? 0) < 0) {
            $errors['checksattempts'] = get_string('checksattemptsinvalid', 'gns3lab');
        }
        [, $bad] = \mod_gns3lab\local\checks::parse_overrides((string) ($data['checksoverridestext'] ?? ''));
        if ($bad) {
            $errors['checksoverridestext'] = get_string(
                'checksoverridesinvalid',
                'gns3lab',
                implode(', ', array_slice($bad, 0, 5))
            );
        }
        if (($data['checksreport'] ?? '') === \mod_gns3lab\local\checks::REPORT_AFTER && empty($data['checksreportdate'])) {
            $errors['checksreportdate'] = get_string('required');
        }
        return $errors;
    }

    /**
     * Checks the starting nodes of a fog against the nodes of the lab that is being chosen.
     *
     * The names are checked only when the lab has a node list to read (a library lab, a reference lab the manager
     * shows); for another lab there is nothing to compare with.
     *
     * @param array $data submitted data
     * @return string the error, empty if there is none
     */
    protected function fogstart_error(array $data): string {
        $given = $data['fogstart'] ?? '';
        $names = is_array($given) ? $given : \mod_gns3lab\local\reveal::parse_names((string) $given);
        $names = \mod_gns3lab\local\reveal::parse_names(\mod_gns3lab\local\reveal::encode_names($names));
        if (!$names) {
            return '';
        }
        $labnames = \mod_gns3lab\local\reveal::lab_node_names((object) [
            'labversionid' => (int) ($data['labversionid'] ?? 0),
            'labref' => (string) ($data['labref'] ?? ''),
        ]);
        $unknown = $labnames === null ? [] : array_diff($names, $labnames);
        return $unknown ? get_string('fogstartunknown', 'gns3lab', implode(', ', $unknown)) : '';
    }
}
