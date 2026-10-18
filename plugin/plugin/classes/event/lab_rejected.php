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
 * The lab manager refused to start the library lab of an activity: the lab no longer fits the site.
 *
 * Happens when the administrator of the lab manager removed an image or cut the site limits after the
 * lab was published. The problems of the lab manager go to the log, the student is told to ask the teacher.
 *
 * @property-read array $other {
 *      - int labversionid: published version the activity uses
 *      - string problems: "path: code" of each problem, separated by "; "
 * }
 *
 * @package    mod_gns3lab
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
class lab_rejected extends \core\event\base {
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
        return get_string('eventlabrejected', 'gns3lab');
    }

    /**
     * Event description for the log.
     *
     * @return string
     */
    public function get_description() {
        return "The lab manager refused to start the lab (library version id '{$this->other['labversionid']}') " .
            "of the activity with course module id '{$this->contextinstanceid}' for the user with id '{$this->userid}'. " .
            "Problems: {$this->other['problems']}";
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
        if (!isset($this->other['labversionid'], $this->other['problems'])) {
            throw new \coding_exception('The \'labversionid\' and \'problems\' values must be set in other.');
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
        return ['labversionid' => \core\event\base::NOT_MAPPED];
    }
}
