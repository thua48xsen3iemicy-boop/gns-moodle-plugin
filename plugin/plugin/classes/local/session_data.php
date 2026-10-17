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

namespace mod_gns3lab\local;

use core_external\external_multiple_structure;
use core_external\external_single_structure;
use core_external\external_value;

/**
 * A lab manager session as returned by the external functions.
 *
 * The manager's JSON is not passed through: only the fields described here reach the browser.
 * The topology is the exception, it goes as a JSON string (the .gns3 format is big and changes
 * between GNS3 versions), the viewer parses it.
 *
 * @package    mod_gns3lab
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
class session_data {
    /** Statuses after which the session does not change any more. */
    public const FINAL_STATUSES = ['stopped', 'failed'];

    /**
     * Description of a session for execute_returns().
     *
     * @param int $required VALUE_REQUIRED or VALUE_OPTIONAL
     * @return external_single_structure
     */
    public static function structure(int $required = VALUE_REQUIRED): external_single_structure {
        return new external_single_structure([
            'sessionid' => new external_value(PARAM_ALPHANUMEXT, 'Session id on the lab manager'),
            'status' => new external_value(PARAM_ALPHA, 'queued, starting, running, stopping, stopped or failed'),
            'stage' => new external_value(PARAM_ALPHANUMEXT, 'Step of the manager inside the status, empty if unknown'),
            'progress' => new external_value(PARAM_INT, 'Percent of the current step, -1 if unknown'),
            'statusmessage' => new external_value(PARAM_TEXT, 'Status text from the lab manager (in Russian)'),
            'errorcode' => new external_value(PARAM_ALPHANUMEXT, 'Why a failed session failed, empty otherwise'),
            'errorretryable' => new external_value(PARAM_BOOL, 'Starting again may help'),
            'startedat' => new external_value(PARAM_INT, 'Start time, 0 if not started'),
            'expiresat' => new external_value(PARAM_INT, 'Time the lab will be stopped, 0 if unknown'),
            'topology' => new external_value(PARAM_RAW, 'Live .gns3 document as JSON, empty unless running'),
            'nodes' => new external_multiple_structure(new external_single_structure([
                'nodeid' => new external_value(PARAM_ALPHANUMEXT, 'Node id in the live project'),
                'status' => new external_value(PARAM_ALPHA, 'started, stopped or suspended'),
                'consoletype' => new external_value(PARAM_ALPHA, 'telnet, vnc or none'),
            ]), 'Node states, empty unless running'),
            'nodesstale' => new external_value(PARAM_BOOL, 'Node states are an old snapshot: the slot did not answer'),
        ], 'Lab session', $required);
    }

    /**
     * Converts a manager session to the structure above.
     *
     * @param \stdClass $session session from manager_client
     * @return array
     */
    public static function export(\stdClass $session): array {
        $running = $session->status === 'running';
        $nodes = [];
        if ($running && isset($session->nodes) && $session->nodes instanceof \stdClass) {
            foreach (get_object_vars($session->nodes) as $nodeid => $node) {
                $nodes[] = [
                    'nodeid' => (string) $nodeid,
                    'status' => (string) ($node->status ?? ''),
                    'consoletype' => (string) ($node->console_type ?? 'none'),
                ];
            }
        }
        $topology = '';
        if ($running && isset($session->topology) && $session->topology instanceof \stdClass) {
            $topology = json_encode($session->topology, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR);
        }
        $error = $session->status === 'failed' && isset($session->error) && $session->error instanceof \stdClass
            ? $session->error : null;
        $progress = $session->progress ?? null;
        return [
            'sessionid' => $session->session_id,
            'status' => $session->status,
            'stage' => is_string($session->stage ?? null) ? $session->stage : '',
            'progress' => is_int($progress) ? max(0, min(100, $progress)) : -1,
            'statusmessage' => (string) ($session->status_message ?? ''),
            // An unknown code counts as not retryable (CONTRACTS §2.5).
            'errorcode' => $error && is_string($error->code ?? null) ? $error->code : '',
            'errorretryable' => $error && ($error->retryable ?? false) === true,
            'startedat' => self::timestamp($session->started_at ?? null),
            'expiresat' => self::timestamp($session->expires_at ?? null),
            'topology' => $topology,
            'nodes' => $nodes,
            'nodesstale' => $running && ($session->nodes_stale ?? false) === true,
        ];
    }

    /**
     * Whether a node belongs to the topology of a running session.
     *
     * @param \stdClass $session session from manager_client
     * @param string $nodeid node id
     * @return bool
     */
    public static function has_node(\stdClass $session, string $nodeid): bool {
        foreach ($session->topology->topology->nodes ?? [] as $node) {
            if (isset($node->node_id) && $node->node_id === $nodeid) {
                return true;
            }
        }
        return false;
    }

    /**
     * Console type of a node, from the node states of the session.
     *
     * @param \stdClass $session session from manager_client
     * @param string $nodeid node id
     * @return string telnet, vnc, none, or an empty string if the manager did not say
     */
    public static function console_type(\stdClass $session, string $nodeid): string {
        $nodes = $session->nodes ?? null;
        if (!$nodes instanceof \stdClass || !isset($nodes->{$nodeid}->console_type)) {
            return '';
        }
        return (string) $nodes->{$nodeid}->console_type;
    }

    /**
     * ISO 8601 time from the manager to a Unix timestamp.
     *
     * @param mixed $value time string or null
     * @return int 0 if missing or unreadable
     */
    protected static function timestamp(mixed $value): int {
        if (!is_string($value) || $value === '') {
            return 0;
        }
        $time = strtotime($value);
        return $time === false ? 0 : $time;
    }
}
