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
 * English strings for mod_gns3lab.
 *
 * @package    mod_gns3lab
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

defined('MOODLE_INTERNAL') || die();

$string['backtoactivity'] = 'Back to the activity page';
$string['cachedef_info'] = 'What the lab manager accepts and offers (GET /api/v1/info)';
$string['cannotobserveuser'] = 'You cannot observe this user\'s lab: the user is not in your groups or cannot use this activity.';
$string['conflictlink'] = 'Open that lab';
$string['consoleclose'] = 'Close the console';
$string['consoleconnected'] = 'Connected. Press Enter to get a prompt.';
$string['consoleconnecting'] = 'Connecting to the console…';
$string['consolecopied'] = 'Copied';
$string['consolecopyfailed'] = 'The browser did not allow copying';
$string['consoleerror'] = 'The console is unavailable.';
$string['consolekeys'] = 'Keys';
$string['consoleneedsrunning'] = 'Start the lab to open device consoles.';
$string['consolenodestopped'] = 'The node is stopped. The console reconnects when the node starts again.';
$string['consolenodesunknown'] = 'The lab does not report node states right now.';
$string['consolenone'] = 'This device has no console.';
$string['consolenotsupported'] = 'Only text consoles can be opened for now.';
$string['consolereadonly'] = 'View only: input is disabled.';
$string['consolereconnect'] = 'Reconnect';
$string['consolereconnected'] = 'Reconnected. Press Enter to get a prompt.';
$string['consolereconnecting'] = 'Connection lost, reconnecting in {$a} s…';
$string['consoles'] = 'Device consoles';
$string['consolesnippets'] = 'Snippets';
$string['consoletabs'] = 'Open consoles';
$string['consolevnclater'] = 'The graphical console will come later.';
$string['demonotice'] = 'The activity has no reference lab on the lab manager yet, so this is a demo lab: an OSPF topology with device facts. Switch the layers L1, L2, L3 and OSPF and try the configuration with mistakes.';
$string['demosource'] = 'demo';
$string['details'] = 'Details';
$string['drop'] = 'Drop the .gns3 file';
$string['facts'] = 'Demo device facts';
$string['facts_help'] = 'What the backend would send about the running lab: interfaces, VLANs, addresses, adjacencies (two demo data sets here)';
$string['factsbroken'] = 'Configuration with mistakes';
$string['factsok'] = 'Correct configuration';
$string['failedadmin'] = 'The lab failed to start. Tell your teacher the code: {$a}';
$string['failedretry'] = 'The lab failed to start. Try starting it again.';
$string['fit'] = 'Fit';
$string['fit_help'] = 'Fit the whole topology (key 0)';
$string['gatewaynotconfigured'] = 'The console gateway URL is not set: ask the site administrator.';
$string['gatewayurl'] = 'Console gateway URL';
$string['gatewayurl_desc'] = 'The browser connects to the console gateway directly, for example wss://gw.example.org. Used when the lab manager does not return the gateway URL with the console ticket.';
$string['gns3lab:addinstance'] = 'Add a new GNS3 lab';
$string['gns3lab:observe'] = 'Observe students\' labs (read only)';
$string['gns3lab:use'] = 'Start own lab and use consoles';
$string['gns3lab:view'] = 'View the GNS3 lab';
$string['hint'] = 'Drag to pan · pinch or wheel to zoom · double tap to zoom in · tap an object for details';
$string['httptimeout'] = 'HTTP timeout';
$string['httptimeout_desc'] = 'Timeout in seconds for requests to the lab manager.';
$string['keybreak'] = 'Ctrl+Shift+6: break a running command (Cisco)';
$string['keyctrl'] = 'Ctrl for the next key';
$string['keyspace'] = 'Space: next page of --More--';
$string['labnotready'] = 'The activity has no reference lab yet: only the demo topology is shown.';
$string['labref'] = 'Reference lab';
$string['labref_help'] = 'Identifier of the reference lab on the lab manager (lab_ref). Leave empty to show the demo lab while the manager is not connected.';
$string['labsettings'] = 'Lab';
$string['layer_l1'] = 'L1 — physical: cables and link state (key 1)';
$string['layer_l2'] = 'L2 — VLANs, trunks and broadcast domains (key 2)';
$string['layer_l3'] = 'L3 — IP subnets and addresses (key 3)';
$string['layer_rt'] = 'Routing — OSPF adjacencies (key 4)';
$string['layers'] = 'Topology layer';
$string['livedata'] = 'Live data';
$string['livedata_help'] = 'Show what the gateway gets from the running lab: node states and Proxmox guests (demo data here)';
$string['managererror'] = 'The lab manager could not complete the request. Try again later.';
$string['managererror_gateway_unavailable'] = 'The console gateway did not respond. Try again in a minute.';
$string['managererror_lab_invalid'] = 'The reference lab of this activity is broken on the lab manager. Tell your teacher.';
$string['managererror_lab_not_found'] = 'The lab manager does not know the reference lab of this activity. Tell your teacher.';
$string['managererror_node_not_found'] = 'This device is not part of the running lab.';
$string['managererror_node_not_running'] = 'The device is stopped.';
$string['managererror_session_conflict'] = 'You already have another lab running. Stop it first.';
$string['managererror_session_not_found'] = 'The lab session is gone. Reload the page.';
$string['managererror_session_not_running'] = 'The lab is not running.';
$string['managererror_unauthorized'] = 'The lab manager rejected this site: check the manager URL and secret in the plugin settings.';
$string['managernotconfigured'] = 'The lab manager is not connected on this site.';
$string['managersecret'] = 'Lab manager secret';
$string['managersecret_desc'] = 'Shared secret for server-to-server requests to the lab manager. Never sent to the browser.';
$string['managerunreachable'] = 'The lab manager is not reachable. Try again later.';
$string['managerurl'] = 'Lab manager URL';
$string['managerurl_desc'] = 'Base URL of the lab manager API, for example https://labs.example.org. Without it activities show the demo lab.';
$string['maxduration'] = 'Session limit, minutes';
$string['maxduration_help'] = 'How long a student\'s lab may run. 0 means the lab manager default.';
$string['maxdurationinvalid'] = 'The limit cannot be negative.';
$string['modulename'] = 'GNS3 lab';
$string['modulename_help'] = 'A GNS3 lab activity shows the lab topology and lets students work in device consoles right on the course page.';
$string['modulenameplural'] = 'GNS3 labs';
$string['nodenotinsession'] = 'This device is not part of the running lab.';
$string['nolabref'] = 'The activity has no reference lab.';
$string['nosession'] = 'There is no lab session to stop.';
$string['openfile'] = 'Open .gns3';
$string['openfullscreen'] = 'Open the lab full screen';
$string['pluginadministration'] = 'GNS3 lab administration';
$string['pluginname'] = 'GNS3 lab';
$string['previewerror'] = 'The reference lab could not be loaded from the lab manager, a demo lab is shown instead.';
$string['privacy:metadata:gns3labmanager'] = 'The lab manager runs the student\'s lab and keeps the student\'s saved project.';
$string['privacy:metadata:gns3labmanager:courseid'] = 'The course id of the activity.';
$string['privacy:metadata:gns3labmanager:instanceid'] = 'The activity id, to find the student\'s project.';
$string['privacy:metadata:gns3labmanager:userid'] = 'The user id, to run and keep the lab of this user.';
$string['readonly'] = 'View only';
$string['readonly_help'] = 'The viewer changes nothing in the lab';
$string['sessionconflictat'] = 'You already have a lab running in "{$a}". Stop it first.';
$string['sessionnotrunning'] = 'The lab is not running.';
$string['stage_ct_park'] = 'Releasing the slot…';
$string['stage_ct_prepare'] = 'Preparing the container…';
$string['stage_ct_start'] = 'Starting the container…';
$string['stage_ct_stop'] = 'Stopping the container…';
$string['stage_lab_stop'] = 'Stopping the devices…';
$string['stage_lab_stop_wait'] = 'Stopping the devices…';
$string['stage_lab_submit'] = 'Creating the lab…';
$string['stage_lab_wait'] = 'Starting the devices…';
$string['stage_ready'] = 'The lab is running';
$string['stage_slot_ready'] = 'Waiting for the slot…';
$string['stage_student_dir'] = 'Preparing your folder…';
$string['stage_wait_slot'] = 'Waiting for a free slot…';
$string['startagain'] = 'Start again';
$string['startlab'] = 'Start the lab';
$string['status_failed'] = 'The lab failed to start';
$string['status_none'] = 'The lab is not running';
$string['status_queued'] = 'Waiting for a free slot…';
$string['status_running'] = 'The lab is running';
$string['status_starting'] = 'Starting…';
$string['status_stopped'] = 'The lab is stopped';
$string['status_stopping'] = 'Stopping…';
$string['stopconfirm'] = 'Your work in this lab will not be saved: the next start begins with a clean lab.';
$string['stopconfirmtitle'] = 'Stop the lab?';
$string['stoplab'] = 'Stop the lab';
$string['timeleft'] = '{$a} left';
$string['topology'] = 'Topology';
$string['zoom'] = 'Zoom';
$string['zoomin'] = 'Zoom in';
$string['zoomout'] = 'Zoom out';
