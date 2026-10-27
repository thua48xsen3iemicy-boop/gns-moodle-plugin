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

    /** How long the answer of GET /api/v1/info is kept, seconds. */
    public const INFO_TTL = 300;

    /** How soon a failed GET /api/v1/info is asked again, seconds. */
    public const INFO_RETRY = 30;

    /** Format of the facts snapshot the plugin reads (docs/live-state.md). */
    public const FACTS_SCHEMA = 'gns3lab.facts/1';

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
     * What the manager accepts and offers (CONTRACTS §2.1), cached for a few minutes.
     *
     * A manager without GET /api/v1/info only knows SPEC §3: then nothing beyond it is sent.
     * The answer is cached per manager URL; a failed request is retried sooner than a good answer expires.
     *
     * @return \stdClass known (bool: the manager answered info), options (string[]), features (string[]),
     *     limits (stdClass|null: per-site limits for lab definitions), lab_formats (string[])
     */
    public function get_info(): \stdClass {
        $cache = \cache::make('mod_gns3lab', 'info');
        $key = 'm' . md5($this->baseurl);
        $hit = $cache->get($key);
        if ($hit && $hit->until > time()) {
            return $hit->info;
        }
        $info = (object) ['known' => false, 'options' => [], 'features' => [], 'limits' => null, 'lab_formats' => []];
        $ttl = self::INFO_TTL;
        try {
            $data = $this->expect_object($this->request('GET', '/api/v1/info'));
            $info->known = true;
            $info->options = self::strings($data->options ?? []);
            $info->features = self::strings($data->features ?? []);
            $info->limits = ($data->limits ?? null) instanceof \stdClass ? $data->limits : null;
            $info->lab_formats = self::strings($data->lab_formats ?? []);
        } catch (manager_exception $e) {
            if (!$e->is_not_found()) {
                $ttl = self::INFO_RETRY;
            }
        }
        $cache->set($key, (object) ['until' => time() + $ttl, 'info' => $info]);
        return $info;
    }

    /**
     * Whether the manager announces a feature in GET /api/v1/info.
     *
     * @param string $feature feature name, such as lab_definitions
     * @return bool
     */
    public function has_feature(string $feature): bool {
        return in_array($feature, $this->get_info()->features, true);
    }

    /**
     * Whether the manager takes lab definitions in the format of the lab library (CONTRACTS §2.6).
     *
     * @return bool
     */
    public function supports_lab_definitions(): bool {
        return $this->has_feature('lab_definitions')
            && in_array(lab_definition::FORMAT, $this->get_info()->lab_formats ?? [], true);
    }

    /**
     * Whether the manager has the lab constructor: lab definitions with segments (gns3lab.lab/2, CONTRACTS §2.6).
     *
     * @return bool
     */
    public function supports_lab_constructor(): bool {
        return $this->supports_lab_definitions()
            && $this->has_feature('lab_constructor')
            && in_array(lab_definition::FORMAT_2, $this->get_info()->lab_formats ?? [], true);
    }

    /**
     * Image catalog for the lab editor (MANAGER_REQUESTS П10), cached like the info.
     *
     * @return \stdClass[] images with id, title, kind, category, symbol, console_type, ram_mb, cpus, adapters,
     *     port_name, ports_per_adapter and, with the lab constructor, preconfig; empty if the manager has no catalog
     */
    public function get_images(): array {
        if (!$this->has_feature('images')) {
            return [];
        }
        $cache = \cache::make('mod_gns3lab', 'info');
        $key = 'i' . md5($this->baseurl);
        $hit = $cache->get($key);
        if ($hit && $hit->until > time()) {
            return $hit->images;
        }
        $data = $this->expect_object($this->request('GET', '/api/v1/images'));
        $images = [];
        foreach (is_array($data->images ?? null) ? $data->images : [] as $image) {
            if ($image instanceof \stdClass && isset($image->id, $image->kind) && is_string($image->id)) {
                $images[] = $image;
            }
        }
        $cache->set($key, (object) ['until' => time() + self::INFO_TTL, 'images' => $images]);
        return $images;
    }

    /**
     * Checks a lab definition without storing it (MANAGER_REQUESTS П12).
     *
     * A definition with problems comes back as manager_exception lab_invalid, with problems in details.
     *
     * @param array $lab lab definition gns3lab.lab/1 or gns3lab.lab/2
     * @return \stdClass lab_def (hash), resources {cores, memory_mb}, warnings [{path, code, message}]
     */
    public function validate_lab(array $lab): \stdClass {
        $result = $this->expect_object($this->request('POST', '/api/v1/labs/validate', ['lab' => $lab]));
        if (!isset($result->lab_def) || !is_string($result->lab_def) || $result->lab_def === '') {
            throw new manager_exception('badresponse', 200, 'validate without lab_def');
        }
        return $result;
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
     * Options and the client description are sent only when GET /api/v1/info says the manager takes them:
     * the manager refuses unknown fields (CONTRACTS §1.2).
     *
     * @param int $userid Moodle user id
     * @param int $instanceid gns3lab instance id
     * @param int $courseid course id
     * @param string|array $lab reference lab (lab_ref) or a lab definition from the lab library
     * @param int $maxduration session limit of the activity in minutes, 0 for the manager default
     * @return \stdClass session
     */
    public function create_session(
        int $userid,
        int $instanceid,
        int $courseid,
        string|array $lab,
        int $maxduration = 0
    ): \stdClass {
        global $CFG;

        $body = [
            'user_id' => $userid,
            'instance_id' => $instanceid,
            is_array($lab) ? 'lab' : 'lab_ref' => $lab,
            'course_id' => $courseid,
        ];
        $info = $this->get_info();
        if ($maxduration > 0 && in_array('max_duration', $info->options, true)) {
            $body['options'] = ['max_duration' => $maxduration];
        }
        if ($info->known) {
            $body['client'] = [
                'plugin_version' => (string) get_config('mod_gns3lab', 'version'),
                'moodle_release' => \core_text::substr((string) $CFG->release, 0, 64),
            ];
        }
        return $this->expect_session($this->request('POST', '/api/v1/sessions', $body));
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
     * Sessions of all students of an activity, for the observer page: live or the last finished one each.
     *
     * @param int $instanceid gns3lab instance id
     * @return \stdClass[] sessions without topology and nodes, with user_id
     */
    public function list_activity_sessions(int $instanceid): array {
        $sessions = $this->request('GET', '/api/v1/sessions', null, ['instance_id' => $instanceid]);
        if (!is_array($sessions)) {
            throw new manager_exception('badresponse', 200, 'sessions is not a list');
        }
        return array_map(fn($s) => $this->expect_session($s), $sessions);
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
     * The last facts snapshot of a session (CONTRACTS §2.7, format gns3lab.facts/1 of docs/live-state.md).
     *
     * The manager answers for a stopped session too: then it is the last saved state.
     * A session without any data yet comes back with rev 0 and no nodes.
     *
     * @param string $sessionid session id
     * @param int|null $sincerev rev the caller already has: the manager answers 304 if nothing changed since
     * @return \stdClass|null snapshot with schema, rev, generated_at, ttl, nodes; null if rev is still $sincerev
     */
    public function get_session_facts(string $sessionid, ?int $sincerev = null): ?\stdClass {
        $query = $sincerev !== null ? ['since_rev' => $sincerev] : [];
        $data = $this->request('GET', '/api/v1/sessions/' . rawurlencode($sessionid) . '/state', null, $query, true);
        if ($data === null) {
            return null;
        }
        $facts = $this->expect_object($data);
        if (($facts->schema ?? null) !== self::FACTS_SCHEMA) {
            throw new manager_exception('badresponse', 200, 'unsupported facts schema');
        }
        if (!($facts->nodes ?? null) instanceof \stdClass) {
            throw new manager_exception('badresponse', 200, 'facts without nodes');
        }
        return $facts;
    }

    /**
     * Asks the manager to collect the facts of a running session now (CONTRACTS §2.7).
     *
     * The manager limits how often it really collects; a session that is not running is session_not_running.
     *
     * @param string $sessionid session id
     */
    public function refresh_session_facts(string $sessionid): void {
        $this->request('POST', '/api/v1/sessions/' . rawurlencode($sessionid) . '/state/refresh');
    }

    /**
     * Sends one request and decodes the JSON answer.
     *
     * @param string $method HTTP method
     * @param string $path path below the base URL, starting with /
     * @param array|null $body JSON body
     * @param array $query query parameters
     * @param bool $notmodified a 304 answer is expected (conditional GET): it is returned as null
     * @return mixed decoded JSON
     */
    protected function request(
        string $method,
        string $path,
        ?array $body = null,
        array $query = [],
        bool $notmodified = false
    ): mixed {
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
        if ($status === 304 && $notmodified) {
            // Not modified has no body.
            return null;
        }
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
        if ($code === 'unsupported_field') {
            // The plugin sent something the manager does not take: a plugin bug, not a user error.
            debugging("mod_gns3lab: the lab manager refused a field of {$method} {$path}: {$message}", DEBUG_NORMAL);
        }
        throw new manager_exception($code, $status, $message, $data instanceof \stdClass ? $data : null);
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
     * Keeps the strings of a decoded JSON list.
     *
     * @param mixed $list decoded JSON
     * @return string[]
     */
    protected static function strings(mixed $list): array {
        return is_array($list) ? array_values(array_filter($list, 'is_string')) : [];
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
