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

use core\http_client;
use GuzzleHttp\Exception\GuzzleException;

/**
 * Server-to-server client of the lab manager API (docs/SPEC.md §3).
 *
 * Every request carries the site secret as a bearer token and the site URL in X-Moodle-Site.
 * Responses are decoded with objects as stdClass, so empty JSON objects in .gns3 data stay objects
 * when they are encoded again for the browser.
 *
 * @package    mod_gns3lab
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
class manager_client {
    /** @var string base URL without the trailing slash */
    protected string $baseurl;

    /** @var string site secret */
    protected string $secret;

    /** @var int timeout of one request, seconds */
    protected int $timeout;

    /** @var http_client|null HTTP client, created on first use unless set */
    protected ?http_client $http = null;

    /**
     * Constructor: reads the plugin settings.
     *
     * Code gets the client from the DI container, \core\di::get(manager_client::class), so tests can replace it.
     */
    public function __construct() {
        $config = get_config('gns3lab');
        $this->baseurl = rtrim(trim((string) ($config->managerurl ?? '')), '/');
        $this->secret = (string) ($config->managersecret ?? '');
        $this->timeout = max(1, (int) ($config->httptimeout ?? 10));
    }

    /**
     * Replaces the HTTP client, for tests with a Guzzle mock handler.
     *
     * @param http_client $http client to use
     */
    public function set_http_client(http_client $http): void {
        $this->http = $http;
    }

    /**
     * Whether the manager URL and secret are set.
     *
     * @return bool
     */
    public static function is_configured(): bool {
        $config = get_config('gns3lab');
        return trim((string) ($config->managerurl ?? '')) !== '' && (string) ($config->managersecret ?? '') !== '';
    }

    /**
     * Reference labs available on the manager.
     *
     * @return \stdClass[] objects with lab_ref, title, description
     */
    public function list_labs(): array {
        $labs = $this->request('GET', '/api/v1/labs');
        if (!is_array($labs)) {
            throw new manager_exception('badresponse', 200, 'labs is not a list');
        }
        return $labs;
    }

    /**
     * The .gns3 document of a reference lab, for the preview before a session starts.
     *
     * @param string $labref reference lab
     * @return \stdClass
     */
    public function get_lab_topology(string $labref): \stdClass {
        return $this->expect_object($this->request('GET', '/api/v1/labs/' . rawurlencode($labref) . '/topology'));
    }

    /**
     * The live session of a user in an activity or, if there is none, the last finished one.
     *
     * @param int $userid Moodle user id
     * @param int $instanceid gns3lab instance id
     * @return \stdClass|null null if the user never had a session there
     */
    public function find_session(int $userid, int $instanceid): ?\stdClass {
        try {
            return $this->expect_session($this->request(
                'GET',
                '/api/v1/sessions',
                null,
                ['user_id' => $userid, 'instance_id' => $instanceid]
            ));
        } catch (manager_exception $e) {
            if ($e->is_not_found()) {
                return null;
            }
            throw $e;
        }
    }

    /**
     * Starts a session or returns the live one of the same activity (idempotent).
     *
     * @param int $userid Moodle user id
     * @param int $instanceid gns3lab instance id
     * @param int $courseid course id
     * @param string $labref reference lab
     * @return \stdClass session
     */
    public function create_session(int $userid, int $instanceid, int $courseid, string $labref): \stdClass {
        return $this->expect_session($this->request('POST', '/api/v1/sessions', [
            'user_id' => $userid,
            'instance_id' => $instanceid,
            'lab_ref' => $labref,
            'course_id' => $courseid,
        ]));
    }

    /**
     * Asks the manager to stop a session.
     *
     * @param string $sessionid session id
     * @return \stdClass session, normally with status "stopping"
     */
    public function stop_session(string $sessionid): \stdClass {
        return $this->expect_session($this->request('POST', '/api/v1/sessions/' . rawurlencode($sessionid) . '/stop'));
    }

    /**
     * Gets a one-time console ticket for the console gateway.
     *
     * @param string $sessionid session id
     * @param string $nodeid node id from the session topology
     * @param bool $readonly observer ticket: the gateway drops input
     * @param int $issuedfor id of the Moodle user the ticket is for, logged by the manager
     * @return \stdClass with ticket, gateway_url (may be missing), expires_in
     */
    public function issue_console_ticket(string $sessionid, string $nodeid, bool $readonly, int $issuedfor): \stdClass {
        $ticket = $this->expect_object($this->request(
            'POST',
            '/api/v1/sessions/' . rawurlencode($sessionid) . '/console-tickets',
            [
                'node_id' => $nodeid,
                'readonly' => $readonly,
                'issued_for_user_id' => $issuedfor,
            ]
        ));
        if (!isset($ticket->ticket) || !is_string($ticket->ticket) || $ticket->ticket === '') {
            throw new manager_exception('badresponse', 200, 'ticket without ticket');
        }
        return $ticket;
    }

    /**
     * Sends one request and decodes the JSON answer.
     *
     * @param string $method HTTP method
     * @param string $path path below the base URL, starting with /
     * @param array|null $body JSON body
     * @param array $query query parameters
     * @return mixed decoded JSON
     */
    protected function request(string $method, string $path, ?array $body = null, array $query = []): mixed {
        global $CFG;

        if ($this->baseurl === '' || $this->secret === '') {
            throw new manager_exception('notconfigured');
        }
        $options = [
            'http_errors' => false,
            'timeout' => $this->timeout,
            'connect_timeout' => min(5, $this->timeout),
            'headers' => [
                'Authorization' => 'Bearer ' . $this->secret,
                'X-Moodle-Site' => $CFG->wwwroot,
                'Accept' => 'application/json',
            ],
        ];
        if ($query) {
            $options['query'] = $query;
        }
        if ($body !== null) {
            $options['json'] = $body;
        }
        try {
            $response = $this->client()->request($method, $this->baseurl . $path, $options);
        } catch (GuzzleException $e) {
            // The message may contain the URL but never the secret: it travels in a header.
            throw new manager_exception('unreachable', 0, $e->getMessage());
        }

        $status = $response->getStatusCode();
        $raw = (string) $response->getBody();
        try {
            $data = $raw === '' ? null : json_decode($raw, false, 512, JSON_THROW_ON_ERROR);
        } catch (\JsonException $e) {
            throw new manager_exception('badresponse', $status, 'response is not JSON');
        }
        if ($status >= 200 && $status < 300) {
            return $data;
        }
        $code = is_object($data) && isset($data->error) && is_string($data->error) ? $data->error : 'http_' . $status;
        $message = is_object($data) && isset($data->message) && is_string($data->message) ? $data->message : '';
        throw new manager_exception($code, $status, $message);
    }

    /**
     * The HTTP client.
     *
     * Moodle's curl security blocks private networks and non-standard ports by default, and the manager
     * normally lives on exactly such an address. Its URL comes from the site administration only,
     * never from users, so the block list is not applied to it.
     *
     * @return http_client
     */
    protected function client(): http_client {
        if ($this->http === null) {
            $this->http = new http_client(['ignoresecurity' => true]);
        }
        return $this->http;
    }

    /**
     * Checks that a decoded answer is an object.
     *
     * @param mixed $data decoded JSON
     * @return \stdClass
     */
    protected function expect_object(mixed $data): \stdClass {
        if (!$data instanceof \stdClass) {
            throw new manager_exception('badresponse', 200, 'object expected');
        }
        return $data;
    }

    /**
     * Checks the fields every session object must have.
     *
     * @param mixed $data decoded JSON
     * @return \stdClass
     */
    protected function expect_session(mixed $data): \stdClass {
        $session = $this->expect_object($data);
        if (!isset($session->session_id, $session->status) || !is_string($session->session_id) || !is_string($session->status)) {
            throw new manager_exception('badresponse', 200, 'session without session_id or status');
        }
        return $session;
    }
}
