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
use mod_gns3lab\local\manager_client;

/**
 * The activity page: a button that opens the lab full screen.
 *
 * The scheme and the consoles live on the full screen page, so this page needs no data from the lab manager.
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
        return [
            'openurl' => (new url('/mod/gns3lab/lab.php', ['id' => $this->cm->id]))->out(false),
            'isdemo' => $nolab || !manager_client::is_configured(),
        ];
    }
}
