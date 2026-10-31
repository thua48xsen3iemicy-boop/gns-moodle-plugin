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

namespace mod_gns3lab\external;

use core_external\external_api;
use core_external\external_function_parameters;
use core_external\external_multiple_structure;
use core_external\external_single_structure;
use core_external\external_value;
use mod_gns3lab\local\image_data;
use mod_gns3lab\local\lab_definition;
use mod_gns3lab\local\lab_library;
use mod_gns3lab\local\manager_client;
use mod_gns3lab\local\manager_exception;
use mod_gns3lab\local\session_data;

/** Authoring operations always resolve the current user's session on the server. */
class authoring extends external_api {
    /** Strict inputs shared by the editor and its authoring console page. */
    public static function execute_parameters(): external_function_parameters {
        return new external_function_parameters([
            'labid' => new external_value(PARAM_INT, 'Library lab ID'),
            'action' => new external_value(PARAM_ALPHA, 'Authoring operation'),
            'definition' => new external_value(PARAM_RAW, 'Definition or expected draft as JSON', VALUE_DEFAULT, ''),
            'nodes' => new external_multiple_structure(new external_single_structure([
                'name' => new external_value(PARAM_TEXT, 'Node name'),
                'title' => new external_value(PARAM_TEXT, 'Image title'),
                'description' => new external_value(PARAM_TEXT, 'Image description', VALUE_DEFAULT, ''),
            ]), 'Selected nodes', VALUE_DEFAULT, []),
            'nodeid' => new external_value(PARAM_ALPHANUMEXT, 'Console node', VALUE_DEFAULT, ''),
            'imageid' => new external_value(PARAM_ALPHANUMEXT, 'Derived image', VALUE_DEFAULT, ''),
            'title' => new external_value(PARAM_TEXT, 'New title', VALUE_DEFAULT, ''),
            'description' => new external_value(PARAM_TEXT, 'New description', VALUE_DEFAULT, ''),
            'featured' => new external_value(PARAM_INT, '-1 unchanged, 0 false, 1 true', VALUE_DEFAULT, -1),
            'retired' => new external_value(PARAM_INT, '-1 unchanged, 0 false, 1 true', VALUE_DEFAULT, -1),
        ]);
    }

    /** Execute one operation after checking system context and capabilities. */
    public static function execute(int $labid, string $action, string $definition = '', array $nodes = [],
            string $nodeid = '', string $imageid = '', string $title = '', string $description = '',
            int $featured = -1, int $retired = -1): array {
        global $USER;
        $params = self::validate_parameters(self::execute_parameters(), compact(
            'labid', 'action', 'definition', 'nodes', 'nodeid', 'imageid', 'title', 'description', 'featured', 'retired'));
        ['labid' => $labid, 'action' => $action, 'definition' => $definition, 'nodes' => $nodes,
            'nodeid' => $nodeid, 'imageid' => $imageid, 'title' => $title, 'description' => $description,
            'featured' => $featured, 'retired' => $retired] = $params;
        $context = lab_library::context();
        self::validate_context($context);
        require_capability('mod/gns3lab:managelabs', $context);
        $lab = lab_library::get_lab($labid);
        $client = \core\di::get(manager_client::class);
        if (!$client->has_feature('image_capture')) {
            throw new \moodle_exception('librarynofeature', 'gns3lab');
        }
        if ($action === 'images') {
            return ['images' => image_data::export($client->get_images(true))];
        }
        if ($action === 'patch') {
            if (!in_array($featured, [-1, 0, 1], true) || !in_array($retired, [-1, 0, 1], true)) {
                throw new \invalid_parameter_exception('Invalid image flags');
            }
            $image = null;
            foreach ($client->get_images(true) as $candidate) {
                if ($candidate->id === $imageid) {
                    $image = $candidate;
                    break;
                }
            }
            if (!$image || !isset($image->derived)) {
                throw new \invalid_parameter_exception('Unknown derived image');
            }
            $changes = [];
            if ($featured !== -1) {
                require_capability('mod/gns3lab:manageimages', $context);
                $changes['featured'] = (bool) $featured;
            }
            if ($title !== '' || $retired !== -1) {
                if ((int) $image->derived->author_user_id !== (int) $USER->id) {
                    throw new \required_capability_exception($context, 'mod/gns3lab:managelabs', 'nopermissions', '');
                }
                if ($title !== '') {
                    $changes['title'] = $title;
                    $changes['description'] = $description;
                }
                if ($retired !== -1) {
                    $changes['retired'] = (bool) $retired;
                }
            }
            $client->patch_image($imageid, $changes);
            return ['images' => image_data::export($client->get_images(true))];
        }
        if ($action === 'start') {
            $def = lab_definition::from_json($definition, $lab->name);
            $draft = lab_library::save_draft($labid, $def);
            $session = $client->create_authoring_session((int) $USER->id, $labid, $def);
            return ['session' => self::session($session), 'version' => (int) $draft->version];
        }
        $session = $client->find_authoring_session((int) $USER->id, $labid);
        if ($action === 'state') {
            $state = ['canuse' => true, 'readonly' => false, 'managerconfigured' => true, 'labready' => true, 'resume' => true];
            if ($session) {
                $state['session'] = self::session($session);
            }
            $result = ['state' => $state, 'capturecandidates' => $session->capture_nodes ?? []];
            if ($session) {
                try {
                    $result['capture'] = self::capture($client->capture_state($session->session_id));
                } catch (manager_exception $e) {
                    if (!$e->is_not_found()) {
                        throw $e;
                    }
                }
            }
            return $result;
        }
        if (!$session) {
            throw new \moodle_exception('consoleneedsrunning', 'gns3lab');
        }
        if ($action === 'stop') {
            return ['session' => self::session($client->stop_session($session->session_id))];
        }
        if ($action === 'ticket') {
            $known = false;
            foreach ($session->topology->topology->nodes ?? [] as $node) {
                $known = $known || $node->node_id === $nodeid;
            }
            if ($session->status !== 'running' || !$known) {
                throw new \moodle_exception('consoleneedsrunning', 'gns3lab');
            }
            $ticket = $client->issue_console_ticket($session->session_id, $nodeid, false, (int) $USER->id);
            $url = rtrim((string) ($ticket->gateway_url ?? get_config('gns3lab', 'gatewayurl')), '/');
            if (!preg_match('~^wss?://[^\s/?#]+(/[^\s?#]*)?$~i', $url)) {
                throw new \moodle_exception('gatewaynotconfigured', 'gns3lab');
            }
            return ['ticket' => ['ticket' => $ticket->ticket, 'gatewayurl' => $url,
                'expiresin' => (int) ($ticket->expires_in ?? 30), 'readonly' => false]];
        }
        if ($action === 'capture') {
            $def = lab_definition::from_json($definition, $lab->name);
            if ($client->validate_lab($def)->lab_def !== $session->lab_def) {
                throw new \moodle_exception('authoringdefinitionchanged', 'gns3lab');
            }
            return ['capture' => self::capture($client->capture_images($session->session_id, $nodes))];
        }
        if ($action === 'capturestate' || $action === 'apply') {
            $capture = $client->capture_state($session->session_id);
            if ($action === 'apply') {
                if ($capture->state !== 'done') {
                    throw new \invalid_parameter_exception('Capture is not complete');
                }
                $def = lab_definition::from_json($definition, $lab->name);
                $expected = lab_definition::to_json($def);
                foreach ($def['nodes'] as &$node) {
                    $node['image'] = $capture->images->{$node['name']} ?? $node['image'];
                }
                unset($node);
                $latest = lab_library::latest_version($labid);
                if (!$latest || $latest->definition !== lab_definition::to_json($def)) {
                    $latest = lab_library::save_draft($labid, $def, $expected);
                }
                return ['capture' => self::capture($capture), 'version' => (int) $latest->version,
                    'images' => image_data::export($client->get_images(true))];
            }
            return ['capture' => self::capture($capture)];
        }
        throw new \invalid_parameter_exception('Unknown authoring action');
    }

    /** The authoring viewer uses the student UI without assessment/facts requests. */
    private static function session(\stdClass $session): array {
        $result = session_data::export($session);
        $result['features'] = array_values(array_diff($result['features'], ['facts']));
        return $result;
    }

    /** Convert the name-to-image map into a strict external structure. */
    private static function capture(\stdClass $capture): array {
        $images = [];
        foreach ($capture->images ?? [] as $name => $id) {
            $images[] = ['name' => $name, 'image' => $id];
        }
        return ['state' => $capture->state, 'images' => $images,
            'errorcode' => $capture->error->code ?? '', 'errornode' => $capture->error->node ?? '',
            'retryable' => (bool) ($capture->error->retryable ?? false)];
    }

    /** Typed union of operation responses; only one operation is executed per call. */
    public static function execute_returns(): external_single_structure {
        $state = get_state::execute_returns();
        $state->required = VALUE_OPTIONAL;
        $ticket = get_console_ticket::execute_returns();
        $ticket->required = VALUE_OPTIONAL;
        return new external_single_structure([
            'session' => session_data::structure(VALUE_OPTIONAL),
            'state' => $state, 'ticket' => $ticket,
            'version' => new external_value(PARAM_INT, 'Draft version', VALUE_OPTIONAL),
            'capturecandidates' => new external_multiple_structure(new external_value(PARAM_TEXT, 'Node name'),
                'Nodes which ran in this session', VALUE_OPTIONAL),
            'capture' => new external_single_structure([
                'state' => new external_value(PARAM_ALPHA, 'running, done or failed'),
                'images' => new external_multiple_structure(new external_single_structure([
                    'name' => new external_value(PARAM_TEXT, 'Node name'),
                    'image' => new external_value(PARAM_ALPHANUMEXT, 'Image ID'),
                ])),
                'errorcode' => new external_value(PARAM_ALPHANUMEXT, 'Failure code'),
                'errornode' => new external_value(PARAM_TEXT, 'Failed node'),
                'retryable' => new external_value(PARAM_BOOL, 'Retry can help'),
            ], 'Capture operation', VALUE_OPTIONAL),
            'images' => image_data::structure(),
        ]);
    }
}
