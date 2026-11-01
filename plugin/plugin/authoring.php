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

/** Authoring console page for a site library lab. */
require('../../config.php');

use mod_gns3lab\local\lab_definition;
use mod_gns3lab\local\lab_library;
use mod_gns3lab\local\manager_client;

$id = required_param('id', PARAM_INT);
require_login(null, false);
$context = lab_library::setup_page(new \core\url('/mod/gns3lab/authoring.php', ['id' => $id]), 'embedded');
$lab = lab_library::get_lab($id);
$version = lab_library::latest_version($id);
$client = \core\di::get(manager_client::class);
if (!$client->has_feature('image_capture')) {
    throw new \moodle_exception('librarynofeature', 'gns3lab');
}
$def = $version ? lab_definition::decode($version->definition) : lab_definition::empty($lab->name);
$data = [
    'topology' => lab_definition::to_topology($def, $client->get_images()),
    'labname' => $lab->name, 'source' => get_string('authoringrun', 'gns3lab'),
    'factsview' => 'live', 'seatmode' => 'off', 'authoring' => $id,
    'definition' => lab_definition::to_json($def),
];
$PAGE->add_body_class('gns3lab-fullscreen');
echo $OUTPUT->header();
echo $OUTPUT->render_from_template('mod_gns3lab/viewer', [
    'labname' => format_string($lab->name, true, ['context' => $context]),
    'backurl' => (new \core\url('/mod/gns3lab/editor.php', ['id' => $id]))->out(false),
    'observing' => false, 'cancontrol' => true, 'cmid' => 0, 'userid' => 0,
    'datajson' => json_encode($data, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR),
]);
echo $OUTPUT->footer();
