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
use mod_gns3lab\local\lab_preview;
use mod_gns3lab\local\manager_client;
use mod_gns3lab\local\reveal;

/**
 * The activity page: a button that opens the lab full screen.
 *
 * The scheme and the consoles live on the full screen page; this page shows only a small read-only scheme of the
 * reference lab, so that a lab is recognised before it is opened.
 *
 * @package    mod_gns3lab
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
class overview implements renderable, templatable {
    /**
     * Constructor.
     *
     * @param \cm_info $cm course module
     * @param \stdClass $instance gns3lab record
     */
    public function __construct(
        /** @var \cm_info course module */
        protected \cm_info $cm,
        /** @var \stdClass gns3lab record */
        protected \stdClass $instance
    ) {
    }

    /**
     * Exports the template context.
     *
     * @param renderer_base $output renderer
     * @return array
     */
    public function export_for_template(renderer_base $output): array {
        // Without a reference lab, or without a lab manager, the lab page shows the demo lab.
        $nolab = empty($this->instance->labversionid) && trim((string) $this->instance->labref) === '';
        $isdemo = $nolab || !manager_client::is_configured();
        return [
            'openurl' => (new url('/mod/gns3lab/lab.php', ['id' => $this->cm->id]))->out(false),
            'isdemo' => $isdemo,
            'previewjson' => $this->preview_json(),
            // Teachers who watch the labs get the page of the whole course next to the button (docs/teacher-dashboard.md §2).
            'courselabsurl' => has_capability('mod/gns3lab:observe', $this->cm->context)
                ? (new url('/mod/gns3lab/course.php', ['id' => $this->cm->course]))->out(false) : '',
        ];
    }

    /**
     * The reference topology for the small scheme on the activity page, so that a lab is recognised before it is opened.
     *
     * In the fog mode a student must uncover the topology in the lab, so only those who observe the labs get it.
     * Only the topology travels: no facts, no states.
     *
     * @return string JSON {topology, source}, empty when there is nothing to show
     */
    protected function preview_json(): string {
        if (reveal::mode($this->instance) === reveal::FOG && !has_capability('mod/gns3lab:observe', $this->cm->context)) {
            return '';
        }
        [$data, $error] = lab_preview::load($this->instance);
        if ($error) {
            // A demo scheme in place of a lab that failed to load would mislead: show none.
            return '';
        }
        if ($data === null) {
            $demo = demo::data();
            $data = (object) ['topology' => $demo->topology, 'source' => get_string('demosource', 'gns3lab')];
        }
        return json_encode(['topology' => $data->topology, 'source' => $data->source],
            JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR);
    }
}
