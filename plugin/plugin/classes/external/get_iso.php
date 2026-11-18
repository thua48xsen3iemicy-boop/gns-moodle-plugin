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
use mod_gns3lab\local\lab_access;
use mod_gns3lab\local\manager_client;
use mod_gns3lab\local\manager_exception;

/**
 * The state of an ISO image the current user asked for (manager CONTRACTS §2.12): building, done or failed.
 *
 * Only the owner of the lab reads it: start_iso is the owner's action, and the operation lives in the slot of the
 * owner's session. Reading takes no seat.
 *
 * @package    mod_gns3lab
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
class get_iso extends external_api {
    /**
     * Parameters.
     *
     * @return external_function_parameters
     */
    public static function execute_parameters(): external_function_parameters {
        return new external_function_parameters([
            'cmid' => new external_value(PARAM_INT, 'Course module id'),
            'operationid' => new external_value(PARAM_ALPHANUMEXT, 'Operation id from start_iso'),
        ]);
    }

    /**
     * Reads the operation.
     *
     * @param int $cmid course module id
     * @param string $operationid operation id
     * @return array
     */
    public static function execute(int $cmid, string $operationid): array {
        ['cmid' => $cmid, 'operationid' => $operationid] = self::validate_parameters(
            self::execute_parameters(),
            ['cmid' => $cmid, 'operationid' => $operationid]
        );
        $access = lab_access::from_cmid($cmid);
        self::validate_context($access->context);
        [$userid] = $access->resolve_user(0);
        $client = \core\di::get(manager_client::class);
        $session = $client->find_session($userid, (int) $access->instance->id);
        if ($session === null || $session->status !== 'running') {
            throw new \moodle_exception('sessionnotrunning', 'gns3lab');
        }
        try {
            return self::export($client->get_iso($session->session_id, $operationid));
        } catch (manager_exception $e) {
            if ($e->is_not_found()) {
                throw new \moodle_exception('isonotfound', 'gns3lab');
            }
            throw $e;
        }
    }

    /**
     * Converts an operation of the manager to the structure of execute_returns().
     *
     * @param \stdClass $op operation from manager_client::start_iso() or get_iso()
     * @return array
     */
    public static function export(\stdClass $op): array {
        $result = $op->result ?? null;
        $result = $result instanceof \stdClass ? $result : null;
        $targets = [];
        foreach (is_array($result->targets ?? null) ? $result->targets : [] as $target) {
            if ($target instanceof \stdClass && is_string($target->node_id ?? null)) {
                $targets[] = [
                    'nodeid' => clean_param($target->node_id, PARAM_ALPHANUMEXT),
                    'status' => ($target->status ?? '') === 'attached' ? 'attached' : 'failed',
                    'errorcode' => clean_param((string) ($target->error ?? ''), PARAM_ALPHANUMEXT),
                ];
            }
        }
        $error = $op->error ?? null;
        $progress = $op->progress ?? null;
        return [
            'operationid' => $op->operation_id,
            'status' => $op->status,
            'stage' => clean_param((string) ($op->stage ?? ''), PARAM_ALPHANUMEXT),
            'progress' => is_int($progress) ? max(0, min(100, $progress)) : -1,
            'errorcode' => $error instanceof \stdClass ? clean_param((string) ($error->code ?? ''), PARAM_ALPHANUMEXT) : '',
            'files' => is_int($result->files ?? null) ? $result->files : 0,
            'bytes' => is_int($result->bytes ?? null) ? $result->bytes : 0,
            'targets' => $targets,
        ];
    }

    /**
     * Result.
     *
     * @return external_single_structure
     */
    public static function execute_returns(): external_single_structure {
        return new external_single_structure([
            'operationid' => new external_value(PARAM_ALPHANUMEXT, 'Operation id'),
            'status' => new external_value(PARAM_ALPHA, 'running, succeeded or failed'),
            'stage' => new external_value(PARAM_ALPHANUMEXT, 'iso_pack, iso_transfer, iso_build or iso_attach while running'),
            'progress' => new external_value(PARAM_INT, 'Percent while running, -1 if unknown'),
            'errorcode' => new external_value(PARAM_ALPHANUMEXT, 'Why it failed (CONTRACTS §2.12), empty otherwise'),
            'files' => new external_value(PARAM_INT, 'Files in the image, 0 until it is built'),
            'bytes' => new external_value(PARAM_INT, 'Size of the image, 0 until it is built'),
            'targets' => new external_multiple_structure(new external_single_structure([
                'nodeid' => new external_value(PARAM_ALPHANUMEXT, 'Target node id'),
                'status' => new external_value(PARAM_ALPHA, 'attached or failed'),
                'errorcode' => new external_value(PARAM_ALPHANUMEXT, 'Why this node did not take the image, empty if it did'),
            ]), 'What happened to each target'),
        ]);
    }
}
