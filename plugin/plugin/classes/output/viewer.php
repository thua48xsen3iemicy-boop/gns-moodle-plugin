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

namespace mod_gns3lab\output;

use core\output\renderable;
use core\output\renderer_base;
use core\output\templatable;
use core\url;
use mod_gns3lab\local\demo;
use mod_gns3lab\local\lab_definition;
use mod_gns3lab\local\lab_library;
use mod_gns3lab\local\manager_client;
use mod_gns3lab\local\manager_exception;

/**
 * Topology viewer, inline on the activity page or full screen.
 *
 * @package    mod_gns3lab
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
class viewer implements renderable, templatable {
    /**
     * Constructor.
     *
     * @param \cm_info $cm course module
     * @param \stdClass $instance gns3lab record
     * @param bool $fullscreen true for the full screen page
     */
    public function __construct(
        /** @var \cm_info course module */
        protected \cm_info $cm,
        /** @var \stdClass gns3lab record */
        protected \stdClass $instance,
        /** @var bool full screen page */
        protected bool $fullscreen
    ) {
    }

    /**
     * Exports the template context.
     *
     * The lab data travels as JSON in a data attribute: it is too big for js_call_amd arguments.
     * The preview is the reference lab from the lab manager; the bundled demo lab is shown
     * when the manager is not set up or the activity has no reference lab yet.
     *
     * @param renderer_base $output renderer
     * @return array
     */
    public function export_for_template(renderer_base $output): array {
        $labname = format_string($this->instance->name, true, ['context' => $this->cm->context]);
        $labref = trim($this->instance->labref);
        $data = null;
        $previewerror = false;
        if (!empty($this->instance->labversionid)) {
            $data = $this->library_preview((int) $this->instance->labversionid);
            $previewerror = $data === null;
        } else if ($labref !== '' && manager_client::is_configured()) {
            try {
                $data = (object) [
                    'topology' => \core\di::get(manager_client::class)->get_lab_topology($labref),
                    'source' => $labref,
                ];
            } catch (manager_exception $e) {
                // The page still works: the demo lab is shown with a warning, the session bar reports the manager.
                $previewerror = true;
                debugging('mod_gns3lab: no preview from the lab manager: ' . $e->debuginfo, DEBUG_DEVELOPER);
            }
        }
        $isdemo = $data === null;
        if ($isdemo) {
            $data = demo::data();
            $data->source = get_string('demosource', 'gns3lab');
        }
        $data->labname = $labname;

        return [
            'fullscreen' => $this->fullscreen,
            'labname' => $labname,
            'fullscreenurl' => (new url('/mod/gns3lab/lab.php', ['id' => $this->cm->id]))->out(false),
            'backurl' => (new url('/mod/gns3lab/view.php', ['id' => $this->cm->id]))->out(false),
            'isdemo' => $isdemo && !$previewerror,
            'previewerror' => $previewerror,
            'cancontrol' => has_capability('mod/gns3lab:use', $this->cm->context),
            'cmid' => $this->cm->id,
            'labref' => $this->instance->labref,
            'datajson' => json_encode($data, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR),
        ];
    }

    /**
     * Preview of a lab from the site lab library: its definition drawn with the image catalog.
     *
     * Without the catalog (manager down or not set up) the nodes are still drawn, with generic icons.
     *
     * @param int $versionid published version
     * @return \stdClass|null topology and source, null if the version is gone
     */
    protected function library_preview(int $versionid): ?\stdClass {
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
