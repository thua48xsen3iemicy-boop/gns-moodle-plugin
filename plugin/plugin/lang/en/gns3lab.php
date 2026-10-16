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
$string['demonotice'] = 'The lab manager is not connected yet, so this is a demo lab: an OSPF topology with device facts. Switch the layers L1, L2, L3 and OSPF and try the configuration with mistakes.';
$string['demosource'] = 'demo';
$string['details'] = 'Details';
$string['drop'] = 'Drop the .gns3 file';
$string['facts'] = 'Demo device facts';
$string['facts_help'] = 'What the backend would send about the running lab: interfaces, VLANs, addresses, adjacencies (two demo data sets here)';
$string['factsbroken'] = 'Configuration with mistakes';
$string['factsok'] = 'Correct configuration';
$string['fit'] = 'Fit';
$string['fit_help'] = 'Fit the whole topology (key 0)';
$string['gatewayurl'] = 'Console gateway URL';
$string['gatewayurl_desc'] = 'The browser connects to the console gateway directly, for example wss://gw.example.org. Not used yet.';
$string['gns3lab:addinstance'] = 'Add a new GNS3 lab';
$string['gns3lab:observe'] = 'Observe students\' labs (read only)';
$string['gns3lab:use'] = 'Start own lab and use consoles';
$string['gns3lab:view'] = 'View the GNS3 lab';
$string['hint'] = 'Drag to pan · pinch or wheel to zoom · double tap to zoom in · tap an object for details';
$string['httptimeout'] = 'HTTP timeout';
$string['httptimeout_desc'] = 'Timeout in seconds for requests to the lab manager.';
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
$string['managersecret'] = 'Lab manager secret';
$string['managersecret_desc'] = 'Shared secret for server-to-server requests to the lab manager. Never sent to the browser. Not used yet.';
$string['managerurl'] = 'Lab manager URL';
$string['managerurl_desc'] = 'Base URL of the lab manager API, for example https://labs.example.org. Not used yet: activities show the demo lab.';
$string['maxduration'] = 'Session limit, minutes';
$string['maxduration_help'] = 'How long a student\'s lab may run. 0 means the lab manager default.';
$string['maxdurationinvalid'] = 'The limit cannot be negative.';
$string['modulename'] = 'GNS3 lab';
$string['modulename_help'] = 'A GNS3 lab activity shows the lab topology and lets students work in device consoles right on the course page.';
$string['modulenameplural'] = 'GNS3 labs';
$string['openfile'] = 'Open .gns3';
$string['openfullscreen'] = 'Open the lab full screen';
$string['pluginadministration'] = 'GNS3 lab administration';
$string['pluginname'] = 'GNS3 lab';
$string['privacy:metadata:gns3labmanager'] = 'The lab manager runs the student\'s lab and keeps the student\'s saved project.';
$string['privacy:metadata:gns3labmanager:courseid'] = 'The course id of the activity.';
$string['privacy:metadata:gns3labmanager:instanceid'] = 'The activity id, to find the student\'s project.';
$string['privacy:metadata:gns3labmanager:userid'] = 'The user id, to run and keep the lab of this user.';
$string['readonly'] = 'View only';
$string['readonly_help'] = 'The viewer changes nothing in the lab';
$string['topology'] = 'Topology';
$string['zoom'] = 'Zoom';
$string['zoomin'] = 'Zoom in';
$string['zoomout'] = 'Zoom out';
