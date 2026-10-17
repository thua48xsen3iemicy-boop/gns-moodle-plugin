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

namespace mod_gns3lab\local;

/**
 * Hook callbacks of mod_gns3lab.
 *
 * @package    mod_gns3lab
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
class hook_callbacks {
    /**
     * Adds the lab library to the primary navigation of lab authors.
     *
     * Lab authors are not site administrators: the library page in the plugin settings is out of their reach.
     *
     * @param \core\hook\navigation\primary_extend $hook the hook
     */
    public static function primary_extend(\core\hook\navigation\primary_extend $hook): void {
        if (during_initial_install() || !isloggedin() || isguestuser()) {
            return;
        }
        if (!has_capability('mod/gns3lab:managelabs', lab_library::context())) {
            return;
        }
        $hook->get_primaryview()->add(
            get_string('library', 'gns3lab'),
            new \core\url('/mod/gns3lab/library.php'),
            \navigation_node::TYPE_CUSTOM,
            null,
            lab_library::NAV_KEY
        );
    }
}
