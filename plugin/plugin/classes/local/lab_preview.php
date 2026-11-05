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
 * The reference topology of an activity, as the lab page and the activity page show it before the lab runs.
 *
 * It comes from the site lab library (a published version) or from the lab manager (`lab_ref`). Without either the caller
 * falls back to the bundled demo lab.
 *
 * @package    mod_gns3lab
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
class lab_preview {
    /**
     * Loads the reference topology of an activity.
     *
     * @param \stdClass $instance gns3lab record
     * @return array{0: ?\stdClass, 1: bool} {topology, source} or null, and whether loading it failed
     */
    public static function load(\stdClass $instance): array {
        $labref = trim((string) $instance->labref);
        if (!empty($instance->labversionid)) {
            $data = self::library((int) $instance->labversionid);
            return [$data, $data === null];
        }
        if ($labref === '' || !manager_client::is_configured()) {
            return [null, false];
        }
        try {
            return [(object) [
                'topology' => \core\di::get(manager_client::class)->get_lab_topology($labref),
                'source' => $labref,
            ], false];
        } catch (manager_exception $e) {
            // The page still works: the demo lab is shown with a warning, the session bar reports the manager.
            debugging('mod_gns3lab: no preview from the lab manager: ' . $e->debuginfo, DEBUG_DEVELOPER);
            return [null, true];
        }
    }

    /**
     * Preview of a lab from the site lab library: its definition drawn with the image catalog.
     *
     * Without the catalog (manager down or not set up) the nodes are still drawn, with generic icons.
     *
     * @param int $versionid published version
     * @return \stdClass|null topology and source, null if the version is gone
     */
    protected static function library(int $versionid): ?\stdClass {
        global $DB;

        $version = $DB->get_record('gns3lab_lab_versions', ['id' => $versionid, 'status' => lab_library::PUBLISHED]);
        if (!$version) {
            return null;
        }
        $images = [];
        if (manager_client::is_configured()) {
            try {
                $images = \core\di::get(manager_client::class)->get_images();
            } catch (manager_exception $e) {
                debugging('mod_gns3lab: no image catalog from the lab manager: ' . $e->debuginfo, DEBUG_DEVELOPER);
            }
        }
        $def = lab_definition::decode($version->definition);
        return (object) [
            'topology' => lab_definition::to_topology($def, $images),
            'source' => get_string('libraryversion', 'gns3lab', $version->version),
        ];
    }
}
