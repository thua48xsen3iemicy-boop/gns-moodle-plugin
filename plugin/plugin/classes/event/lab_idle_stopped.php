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


namespace mod_gns3lab\event;

/**
 * A lab was stopped because its student had no lab page open for too long (docs/idle-stop.md).
 *
 * @property-read array $other {
 *      - int lastseen: when the lab page of the student was last seen
 * }
 *
 * @package    mod_gns3lab
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
class lab_idle_stopped extends \core\event\base {
    /**
     * Sets the basic properties.
     */
    protected function init() {
        $this->data['crud'] = 'u';
        $this->data['edulevel'] = self::LEVEL_OTHER;
        $this->data['objecttable'] = 'gns3lab';
    }

    /**
     * Event name.
     *
     * @return string
     */
    public static function get_name() {
        return get_string('eventlabidlestopped', 'gns3lab');
    }

    /**
     * Event description for the log.
     *
     * @return string
     */
    public function get_description() {
        return "The lab of the user with id '{$this->relateduserid}' in the activity with course module id " .
            "'{$this->contextinstanceid}' was stopped: the lab page of the user was last seen at " .
            gmdate('Y-m-d H:i:s', (int) $this->other['lastseen']) . ' UTC.';
    }

    /**
     * Activity page.
     *
     * @return \core\url
     */
    public function get_url() {
        return new \core\url('/mod/gns3lab/view.php', ['id' => $this->contextinstanceid]);
    }

    /**
     * Checks the data.
     */
    protected function validate_data() {
        parent::validate_data();
        if (!isset($this->other['lastseen'])) {
            throw new \coding_exception('The \'lastseen\' value must be set in other.');
        }
        if ($this->relateduserid === null) {
            throw new \coding_exception('The \'relateduserid\' must be set.');
        }
    }

    /**
     * Maps the object id for backup and restore.
     *
     * @return array
     */
    public static function get_objectid_mapping() {
        return ['db' => 'gns3lab', 'restore' => 'gns3lab'];
    }

    /**
     * Other fields that need mapping on restore.
     *
     * @return array
     */
    public static function get_other_mapping() {
        return ['lastseen' => \core\event\base::NOT_MAPPED];
    }
}
