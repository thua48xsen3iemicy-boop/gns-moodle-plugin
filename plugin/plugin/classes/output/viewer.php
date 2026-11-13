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
use mod_gns3lab\local\checks;
use mod_gns3lab\local\demo;
use mod_gns3lab\local\lab_preview;
use mod_gns3lab\local\reveal;
use mod_gns3lab\local\seat;

/**
 * The lab page of a student: the data for the full screen interface (topology, node states, facts).
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
     * @param \stdClass|null $observed the student whose lab a teacher looks at (read only), null for the user's own lab
     * @param bool $fromcourse the teacher came from the course page: the way back leads there
     */
    public function __construct(
        /** @var \cm_info course module */
        protected \cm_info $cm,
        /** @var \stdClass gns3lab record */
        protected \stdClass $instance,
        /** @var \stdClass|null the observed student */
        protected ?\stdClass $observed = null,
        /** @var bool the teacher came from the course page */
        protected bool $fromcourse = false
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
        [$data, $previewerror] = lab_preview::load($this->instance);
        $isdemo = $data === null;
        if ($isdemo) {
            $data = demo::data();
            $data->source = get_string('demosource', 'gns3lab');
        }
        $data->labname = $labname;
        // What the scheme shows a student (live, seen or fog) and, for a fog, the names of the nodes it starts from.
        $data->factsview = reveal::mode($this->instance);
        $data->fogstart = reveal::start_names($this->instance);
        // Who may drive the lab at the same time (docs/seat.md): the page claims the seat unless the mode is `off`.
        $data->seatmode = seat::mode($this->instance);
        // No pasting into the consoles: an option of the activity for students. Whoever may look at the labs of others
        // (a teacher) is not held by it. The page only draws it: the gateway takes whatever a browser sends.
        $data->nopaste = !empty($this->instance->nopaste) && !has_capability('mod/gns3lab:observe', $this->cm->context);
        // Auto-checks (docs/autochecks.md): the page asks for the details (mod_gns3lab_get_checks) only when they are on.
        $data->checks = checks::enabled($this->instance);
        $mode = $data->factsview;
        $back = $this->fromcourse && $this->observed
            ? new url('/mod/gns3lab/course.php', ['id' => $this->cm->course])
            : new url('/mod/gns3lab/view.php', ['id' => $this->cm->id]);

        return [
            'labname' => $labname,
            'assignmenthtml' => trim($this->instance->intro ?? '') !== ''
                ? format_module_intro('gns3lab', $this->instance, $this->cm->id) : '',
            'backurl' => $back->out(false),
            'observing' => $this->observed !== null,
            'studentname' => $this->observed ? fullname($this->observed) : '',
            'userid' => $this->observed ? (int) $this->observed->id : 0,
            'isdemo' => $isdemo && !$previewerror,
            'previewerror' => $previewerror,
            // The session bar also carries the polling of the state, so an observer gets it, without the buttons.
            'cancontrol' => $this->observed !== null || has_capability('mod/gns3lab:use', $this->cm->context),
            'cmid' => $this->cm->id,
            'modetag' => $mode === reveal::LIVE ? '' : get_string('ui_mode_' . $mode . '_tag', 'gns3lab'),
            'modehelp' => $mode === reveal::LIVE ? '' : get_string('ui_mode_' . $mode . '_help', 'gns3lab'),
            'datajson' => json_encode($data, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR),
        ];
    }
}
