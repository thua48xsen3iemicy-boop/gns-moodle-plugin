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
 * Creates a library lab, changes its name, description and tags, or deletes it.
 *
 * @package    mod_gns3lab
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

require('../../config.php');
require_once($CFG->libdir . '/formslib.php');

use mod_gns3lab\local\lab_library;

$id = optional_param('id', 0, PARAM_INT);
$delete = optional_param('delete', 0, PARAM_BOOL);
$confirm = optional_param('confirm', 0, PARAM_BOOL);

require_login(null, false);
$context = lab_library::setup_page(new \core\url('/mod/gns3lab/labedit.php', $id ? ['id' => $id] : null));
$libraryurl = new \core\url('/mod/gns3lab/library.php');
$lab = $id ? lab_library::get_lab($id) : null;

if ($lab && $delete) {
    $PAGE->set_url(new \core\url('/mod/gns3lab/labedit.php', ['id' => $lab->id, 'delete' => 1]));
    $name = format_string($lab->name, true, ['context' => $context]);
    if ($confirm && confirm_sesskey()) {
        lab_library::delete_lab($lab->id);
        redirect($libraryurl, get_string('labdeleted', 'gns3lab', $name), null, \core\output\notification::NOTIFY_SUCCESS);
    }
    if (lab_library::usage([$lab->id])[$lab->id] ?? 0) {
        redirect($libraryurl, get_string('labinuse', 'gns3lab'), null, \core\output\notification::NOTIFY_ERROR);
    }
    $PAGE->set_title(get_string('labdelete', 'gns3lab'));
    echo $OUTPUT->header();
    echo $OUTPUT->confirm(
        get_string('labdeleteconfirm', 'gns3lab', $name),
        new \core\url('/mod/gns3lab/labedit.php', ['id' => $lab->id, 'delete' => 1, 'confirm' => 1, 'sesskey' => sesskey()]),
        $libraryurl
    );
    echo $OUTPUT->footer();
    die();
}

$form = new \mod_gns3lab\form\lab_form(null, ['isnew' => !$lab]);
if ($lab) {
    $data = clone $lab;
    $data->description_editor = ['text' => $lab->description, 'format' => $lab->descriptionformat];
    $data->tags = core_tag_tag::get_item_tags_array('mod_gns3lab', lab_library::TAG_ITEMTYPE, $lab->id);
    $form->set_data($data);
}
if ($form->is_cancelled()) {
    redirect($libraryurl);
}
if ($data = $form->get_data()) {
    $data->description = $data->description_editor['text'];
    $data->descriptionformat = $data->description_editor['format'];
    if ($lab) {
        lab_library::update_lab($data);
        redirect($libraryurl);
    }
    $labid = lab_library::create_lab($data);
    redirect(new \core\url('/mod/gns3lab/editor.php', ['id' => $labid]));
}

$title = $lab ? get_string('labproperties', 'gns3lab') : get_string('labnew', 'gns3lab');
$PAGE->set_title($title);
$PAGE->navbar->add($title);
echo $OUTPUT->header();
echo $OUTPUT->heading($title);
$form->display();
echo $OUTPUT->footer();
