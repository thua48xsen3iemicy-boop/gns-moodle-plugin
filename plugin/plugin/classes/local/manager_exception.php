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

/**
 * A request to the lab manager failed.
 *
 * The user sees a localised message chosen by the manager error code; the manager's own message
 * goes to the debug info only, so nothing from the manager is shown without escaping.
 *
 * @package    mod_gns3lab
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
class manager_exception extends \moodle_exception {
    /** Manager error codes that have their own message in the language pack. */
    private const KNOWN = [
        'lab_not_found', 'lab_invalid', 'session_conflict', 'session_not_found', 'session_not_running',
        'node_not_found', 'node_not_running', 'gateway_unavailable', 'unauthorized',
    ];

    /**
     * Constructor.
     *
     * @param string $remotecode error code from the manager ({"error": ...}), or a local code:
     *     notconfigured, unreachable, badresponse
     * @param int $httpstatus HTTP status, 0 if there was no response
     * @param string $remotemessage message from the manager, for the debug info only
     * @param \stdClass|null $details the whole error body, for extra fields such as conflict
     */
    public function __construct(
        /** @var string error code from the manager or a local code */
        public readonly string $remotecode,
        /** @var int HTTP status, 0 if there was no response */
        public readonly int $httpstatus = 0,
        string $remotemessage = '',
        /** @var \stdClass|null the whole error body from the manager */
        public readonly ?\stdClass $details = null
    ) {
        $key = match (true) {
            in_array($remotecode, self::KNOWN, true) => 'managererror_' . $remotecode,
            $remotecode === 'notconfigured' => 'managernotconfigured',
            $remotecode === 'unreachable' => 'managerunreachable',
            default => 'managererror',
        };
        $debug = trim("{$httpstatus} {$remotecode}: {$remotemessage}");
        parent::__construct($key, 'gns3lab', '', null, $debug);
    }

    /**
     * Whether the manager answered "not found" for the requested object.
     *
     * @return bool
     */
    public function is_not_found(): bool {
        return $this->httpstatus === 404;
    }
}
