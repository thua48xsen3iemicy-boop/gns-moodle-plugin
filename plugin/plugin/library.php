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

/**
 * Site lab library: labs that teachers choose in their activities, prepared by lab authors.
 *
 * @package    mod_gns3lab
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

require('../../config.php');

use mod_gns3lab\local\lab_library;
use mod_gns3lab\local\manager_client;

$tag = optional_param('tag', '', PARAM_TAG);

require_login(null, false);
$context = lab_library::setup_page(new \core\url('/mod/gns3lab/library.php', $tag === '' ? null : ['tag' => $tag]));

$labs = [];
foreach (lab_library::list_labs($tag) as $lab) {
    $labs[] = [
        'name' => format_string($lab->name, true, ['context' => $context]),
        'tags' => array_map(fn($t) => [
            'name' => $t,
            'url' => (new \core\url('/mod/gns3lab/library.php', ['tag' => $t]))->out(false),
        ], $lab->tags),
        'published' => $lab->published ? (int) $lab->published->version : 0,
        'hasdraft' => $lab->draft !== null && $lab->published !== null,
        'usage' => $lab->usage,
        'modified' => userdate($lab->timemodified, get_string('strftimedatetimeshort', 'langconfig')),
        'editorurl' => (new \core\url('/mod/gns3lab/editor.php', ['id' => $lab->id]))->out(false),
        'editurl' => (new \core\url('/mod/gns3lab/labedit.php', ['id' => $lab->id]))->out(false),
        'deleteurl' => $lab->usage ? null
            : (new \core\url('/mod/gns3lab/labedit.php', ['id' => $lab->id, 'delete' => 1]))->out(false),
    ];
}

$managerwarning = null;
if (!manager_client::is_configured()) {
    $managerwarning = get_string('librarynomanager', 'gns3lab');
} else if (!\core\di::get(manager_client::class)->has_feature('lab_definitions')) {
    $managerwarning = get_string('librarynofeature', 'gns3lab');
}

echo $OUTPUT->header();
echo $OUTPUT->render_from_template('mod_gns3lab/library', [
    'labs' => $labs,
    'haslabs' => (bool) $labs,
    'tag' => $tag,
    'clearurl' => (new \core\url('/mod/gns3lab/library.php'))->out(false),
    'newurl' => (new \core\url('/mod/gns3lab/labedit.php'))->out(false),
    'managerwarning' => $managerwarning,
]);
echo $OUTPUT->footer();
