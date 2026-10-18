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
 * Lab editor: the topology of a library lab, drawn from the image catalog of the lab manager.
 *
 * @package    mod_gns3lab
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

require('../../config.php');

use mod_gns3lab\local\lab_definition;
use mod_gns3lab\local\lab_library;
use mod_gns3lab\local\manager_client;
use mod_gns3lab\local\manager_exception;

$id = required_param('id', PARAM_INT);

require_login(null, false);
$context = lab_library::setup_page(new \core\url('/mod/gns3lab/editor.php', ['id' => $id]), 'embedded');

$lab = lab_library::get_lab($id);
$version = lab_library::latest_version($lab->id);
$def = $version ? lab_definition::decode($version->definition) : lab_definition::empty($lab->name);

$images = [];
$limits = null;
$canpublish = false;
$catalogerror = '';
if (!manager_client::is_configured()) {
    $catalogerror = get_string('librarynomanager', 'gns3lab');
} else {
    $client = \core\di::get(manager_client::class);
    try {
        $images = $client->get_images();
        $limits = $client->get_info()->limits;
        $canpublish = $client->supports_lab_definitions();
        if (!$client->has_feature('images')) {
            $catalogerror = get_string('librarynofeature', 'gns3lab');
        }
    } catch (manager_exception $e) {
        $catalogerror = $e->getMessage();
    }
}

// The editor texts go with the data: the editor needs all of them at once.
$strings = [];
$keys = [
    'ed_adapters', 'ed_adaptersinuse', 'ed_catalog', 'ed_checking', 'ed_deletelink',
    'ed_deletenode', 'ed_draft', 'ed_empty', 'ed_free', 'ed_hint', 'ed_hintlink', 'ed_hintlinkto',
    'ed_invalid', 'ed_lab', 'ed_link', 'ed_linkends', 'ed_maxnodes', 'ed_name', 'ed_nameinvalid',
    'ed_nametaken', 'ed_noport', 'ed_ports', 'ed_problem', 'ed_problems', 'ed_published', 'ed_publishing',
    'ed_ram', 'ed_range', 'ed_cpus', 'ed_saved', 'ed_saving', 'ed_summary', 'ed_unknownimage', 'ed_unsaved', 'ed_valid',
    'ed_version', 'ed_cannotpublish',
];
$codes = [
    'required', 'unknown_field', 'bad_type', 'bad_format', 'bad_name', 'duplicate_name', 'unknown_image',
    'image_not_allowed', 'out_of_range', 'unknown_node', 'bad_port', 'port_in_use', 'self_link', 'too_many_nodes',
    'limit_exceeded', 'too_large',
];
foreach ($keys as $key) {
    $strings[$key] = get_string($key, 'gns3lab');
}
foreach ($codes as $code) {
    $strings['problem_' . $code] = get_string('ed_problem_' . $code, 'gns3lab');
}

$data = [
    'labid' => (int) $lab->id,
    'definition' => $def,
    'version' => $version ? (int) $version->version : 1,
    'status' => $version ? $version->status : lab_library::DRAFT,
    'images' => $images,
    'limits' => $limits,
    'canpublish' => $canpublish,
    'catalogerror' => $catalogerror,
    'strings' => $strings,
];

$name = format_string($lab->name, true, ['context' => $context]);
$PAGE->set_title(get_string('labeditortitle', 'gns3lab', $name));
$PAGE->add_body_class('gns3lab-fullscreen');

echo $OUTPUT->header();
echo $OUTPUT->render_from_template('mod_gns3lab/editor', [
    'labname' => $name,
    'libraryurl' => (new \core\url('/mod/gns3lab/library.php'))->out(false),
    'canpublish' => $canpublish,
    'datajson' => json_encode($data, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR),
]);
echo $OUTPUT->footer();
