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
 * External functions of mod_gns3lab, called by the activity page through core/ajax.
 *
 * @package    mod_gns3lab
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

defined('MOODLE_INTERNAL') || die();

$functions = [
    'mod_gns3lab_get_state' => [
        'classname' => \mod_gns3lab\external\get_state::class,
        'description' => 'Current lab session of the user or of an observed student.',
        'type' => 'read',
        'ajax' => true,
        'loginrequired' => true,
    ],
    'mod_gns3lab_start_session' => [
        'classname' => \mod_gns3lab\external\start_session::class,
        'description' => 'Starts the lab of the current user.',
        'type' => 'write',
        'ajax' => true,
        'loginrequired' => true,
    ],
    'mod_gns3lab_stop_session' => [
        'classname' => \mod_gns3lab\external\stop_session::class,
        'description' => 'Stops the lab of the current user.',
        'type' => 'write',
        'ajax' => true,
        'loginrequired' => true,
    ],
    'mod_gns3lab_get_console_ticket' => [
        'classname' => \mod_gns3lab\external\get_console_ticket::class,
        'description' => 'One-time ticket to open a node console through the console gateway.',
        'type' => 'write',
        'ajax' => true,
        'loginrequired' => true,
    ],
    'mod_gns3lab_get_facts' => [
        'classname' => \mod_gns3lab\external\get_facts::class,
        'description' => 'Facts about the devices of the running lab of the user or of an observed student.',
        'type' => 'read',
        'ajax' => true,
        'loginrequired' => true,
    ],
    'mod_gns3lab_refresh_facts' => [
        'classname' => \mod_gns3lab\external\refresh_facts::class,
        'description' => 'Asks the lab manager to collect the facts of the running lab now.',
        'type' => 'write',
        'ajax' => true,
        'loginrequired' => true,
    ],
    'mod_gns3lab_get_course_overview' => [
        'classname' => \mod_gns3lab\external\get_course_overview::class,
        'description' => 'Students by labs of a course with the state of their sessions, for the course page of a teacher.',
        'type' => 'read',
        'ajax' => true,
        'loginrequired' => true,
    ],
    'mod_gns3lab_get_reveal' => [
        'classname' => \mod_gns3lab\external\get_reveal::class,
        'description' => 'What the user or an observed student has uncovered on the scheme in the seen and fog modes.',
        'type' => 'read',
        'ajax' => true,
        'loginrequired' => true,
    ],
    'mod_gns3lab_reveal' => [
        'classname' => \mod_gns3lab\external\reveal::class,
        'description' => 'Adds what the current user has uncovered on the scheme in the seen and fog modes.',
        'type' => 'write',
        'ajax' => true,
        'loginrequired' => true,
    ],
    'mod_gns3lab_claim_seat' => [
        'classname' => \mod_gns3lab\external\claim_seat::class,
        'description' => 'Claims the seat of the current user in an activity (one page drives a lab) or says who holds it.',
        'type' => 'write',
        'ajax' => true,
        'loginrequired' => true,
    ],
    'mod_gns3lab_save_lab_draft' => [
        'classname' => \mod_gns3lab\external\save_lab_draft::class,
        'description' => 'Saves the draft of a library lab from the lab editor.',
        'type' => 'write',
        'ajax' => true,
        'loginrequired' => true,
    ],
    'mod_gns3lab_validate_lab' => [
        'classname' => \mod_gns3lab\external\validate_lab::class,
        'description' => 'Asks the lab manager to check a lab definition.',
        'type' => 'read',
        'ajax' => true,
        'loginrequired' => true,
    ],
    'mod_gns3lab_publish_lab' => [
        'classname' => \mod_gns3lab\external\publish_lab::class,
        'description' => 'Publishes a new version of a library lab.',
        'type' => 'write',
        'ajax' => true,
        'loginrequired' => true,
    ],
    'mod_gns3lab_authoring' => [
        'classname' => \mod_gns3lab\external\authoring::class,
        'description' => 'Runs a library lab, captures its images and manages derived image metadata.',
        'type' => 'write',
        'ajax' => true,
        'loginrequired' => true,
    ],
];
