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
 * Another login of the same account asked for a lab that is being worked in (docs/seat.md).
 *
 * This is the trace of a second person on an account, or of the student's own second device. The log stores the
 * address of the request next to the event. A refusal because of another tab of the same login is not logged.
 *
 * @property-read array $other {
 *      - string mode: seat mode of the activity (tab, login)
 *      - string where: page (the lab page asked for the seat) or action (a call that needs it was refused)
 * }
 *
 * @package    mod_gns3lab
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
class seat_denied extends \core\event\base {
    /**
     * Sets the basic properties.
     */
    protected function init() {
        $this->data['crud'] = 'r';
        $this->data['edulevel'] = self::LEVEL_OTHER;
        $this->data['objecttable'] = 'gns3lab';
    }

    /**
     * Event name.
     *
     * @return string
     */
    public static function get_name() {
        return get_string('eventseatdenied', 'gns3lab');
    }

    /**
     * Event description for the log.
     *
     * @return string
     */
    public function get_description() {
        return "The user with id '{$this->userid}' was refused the lab of the user with id '{$this->relateduserid}' in the " .
            "activity with course module id '{$this->contextinstanceid}': it is in use in another login session " .
            "(seat mode '{$this->other['mode']}', asked by {$this->other['where']}).";
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
        if (!isset($this->other['mode'], $this->other['where'])) {
            throw new \coding_exception('The \'mode\' and \'where\' values must be set in other.');
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
        return ['mode' => \core\event\base::NOT_MAPPED, 'where' => \core\event\base::NOT_MAPPED];
    }
}
