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
     *
     * @param renderer_base $output renderer
     * @return array
     */
    public function export_for_template(renderer_base $output): array {
        $data = demo::data();
        $data->labname = format_string($this->instance->name, true, ['context' => $this->cm->context]);
        $data->source = get_string('demosource', 'gns3lab');

        return [
            'fullscreen' => $this->fullscreen,
            'labname' => $data->labname,
            'fullscreenurl' => (new url('/mod/gns3lab/lab.php', ['id' => $this->cm->id]))->out(false),
            'backurl' => (new url('/mod/gns3lab/view.php', ['id' => $this->cm->id]))->out(false),
            'isdemo' => true,
            'labref' => $this->instance->labref,
            'datajson' => json_encode($data, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR),
        ];
    }
}
