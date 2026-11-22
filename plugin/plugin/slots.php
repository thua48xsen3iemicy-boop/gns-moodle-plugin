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
 * Busy slots: who runs which lab in which slot right now, for the site administrator.
 *
 * @package    mod_gns3lab
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

require('../../config.php');
require_once($CFG->libdir . '/adminlib.php');

use mod_gns3lab\local\live_slots;
use mod_gns3lab\local\manager_client;
use mod_gns3lab\local\manager_exception;

// The external page checks moodle/site:config.
admin_externalpage_setup('gns3labslots');
$pageurl = new \core\url('/mod/gns3lab/slots.php');

$stop = optional_param('stop', '', PARAM_ALPHANUMEXT);
$confirm = optional_param('confirm', 0, PARAM_BOOL);
if ($stop !== '' && manager_client::is_configured() && !$confirm) {
    // The administrator confirms first: the student loses the running lab (the project is kept).
    echo $OUTPUT->header();
    echo $OUTPUT->confirm(
        get_string('liveslotsstopconfirm', 'gns3lab', s(optional_param('who', '', PARAM_TEXT))),
        new \core\url($pageurl, ['stop' => $stop, 'confirm' => 1, 'sesskey' => sesskey()]),
        $pageurl
    );
    echo $OUTPUT->footer();
    die;
}
if ($stop !== '' && manager_client::is_configured()) {
    require_sesskey();
    $client = \core\di::get(manager_client::class);
    try {
        // Only a session that is live on this site now: the id from the form names nothing else.
        if (!live_slots::is_live($client, $stop)) {
            redirect($pageurl, get_string('liveslotsstopgone', 'gns3lab'), null, \core\output\notification::NOTIFY_INFO);
        }
        $client->stop_session($stop, (int) $USER->id);
    } catch (manager_exception $e) {
        redirect($pageurl, $e->getMessage(), null, \core\output\notification::NOTIFY_ERROR);
    }
    redirect($pageurl, get_string('liveslotsstopped', 'gns3lab'), null, \core\output\notification::NOTIFY_SUCCESS);
}

$context = ['rows' => [], 'hasrows' => false, 'slots' => null, 'warning' => null, 'refreshurl' => $pageurl->out(false)];
if (!manager_client::is_configured()) {
    $context['warning'] = get_string('liveslotsnomanager', 'gns3lab');
} else {
    $client = \core\di::get(manager_client::class);
    try {
        if (!$client->has_feature(live_slots::FEATURE)) {
            $context['warning'] = get_string('liveslotsnofeature', 'gns3lab');
        } else {
            ['slots' => $slots, 'rows' => $rows] = live_slots::load($client);
            $context['rows'] = array_map(fn($r) => $r + ['stopurl' => (new \core\url($pageurl, [
                'stop' => $r['sessionid'],
                'who' => $r['username'] !== '' ? $r['fullname'] . ' (' . $r['username'] . ')' : $r['fullname'],
            ]))->out(false)], $rows);
            $context['hasrows'] = (bool) $rows;
            $own = count(array_filter($rows, fn($r) => $r['slot'] !== null));
            $context['slots'] = [
                'total' => $slots->total,
                'free' => $slots->free,
                'unavailable' => $slots->unavailable,
                'own' => $own,
                'other' => max(0, $slots->total - $slots->free - $slots->unavailable - $own),
                'queued' => count($rows) - $own,
            ];
        }
    } catch (manager_exception $e) {
        $context['warning'] = get_string('liveslotsunreachable', 'gns3lab', $e->getMessage());
    }
}

echo $OUTPUT->header();
echo \mod_gns3lab\local\maintenance::notification();
echo $OUTPUT->render_from_template('mod_gns3lab/slots', $context);
echo $OUTPUT->footer();
