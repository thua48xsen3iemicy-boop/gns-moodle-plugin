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

namespace mod_gns3lab\tests;

use core\http_client;
use GuzzleHttp\Promise\Create;
use GuzzleHttp\Psr7\Response;
use mod_gns3lab\local\manager_client;
use Psr\Http\Message\RequestInterface;

/**
 * In-memory lab manager for tests: canned answers by method and path, every request recorded.
 *
 * @package    mod_gns3lab
 * @category   test
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
class fake_manager {
    /** Node with a telnet console in the running session. */
    public const NODE_TELNET = '6b1f3c2e-4a7d-4e5b-9c10-000000000001';

    /** Node with a VNC console in the running session. */
    public const NODE_VNC = '6b1f3c2e-4a7d-4e5b-9c10-000000000002';

    /** Facts route of the running session. */
    public const STATE = '/api/v1/sessions/s-3f9a1c0d2b4e5f60/state';

    /** @var array<string, array{int, mixed}> "METHOD /path" => [status, body] */
    protected array $routes = [];

    /** @var array<int, array{method: string, path: string, query: string, headers: array, body: mixed}> requests */
    public array $requests = [];

    /**
     * Sets the plugin settings and puts a client talking to this fake into the DI container.
     *
     * @return manager_client
     */
    public function install(): manager_client {
        set_config('managerurl', 'https://manager.test/', 'gns3lab');
        set_config('managersecret', 'site-secret', 'gns3lab');
        $client = new manager_client();
        $client->set_http_client(new http_client([
            'mock' => fn(RequestInterface $request) => Create::promiseFor($this->handle($request)),
            'ignoresecurity' => true,
        ]));
        \core\di::set(manager_client::class, $client);
        return $client;
    }

    /**
     * Sets the answer to a request.
     *
     * @param string $method HTTP method
     * @param string $path path, with the query to answer only requests with exactly that query
     * @param int $status HTTP status
     * @param mixed $body answer, encoded as JSON unless it is a string
     */
    public function on(string $method, string $path, int $status, mixed $body): void {
        $this->routes[$method . ' ' . $path] = [$status, $body];
    }

    /**
     * Answers the facts routes of the running session like manager 0.8 (CONTRACTS §2.7).
     *
     * GET with since_rev equal to the rev of the snapshot gets 304 without a body.
     *
     * @param array|null $facts snapshot, facts() by default
     */
    public function with_facts(?array $facts = null): void {
        $this->on('GET', self::STATE, 200, $facts ?? self::facts());
        $this->on('POST', self::STATE . '/refresh', 202, '');
    }

    /**
     * A facts snapshot gns3lab.facts/1 of the running session.
     *
     * @param int $rev rev of the snapshot
     * @return array
     */
    public static function facts(int $rev = 5): array {
        return [
            'schema' => 'gns3lab.facts/1',
            'rev' => $rev,
            'generated_at' => gmdate('Y-m-d\\TH:i:s\\Z'),
            'ttl' => 90,
            'nodes' => [
                self::NODE_TELNET => [
                    'name' => 'SW1',
                    'status' => 'started',
                    'collected_at' => gmdate('Y-m-d\\TH:i:s\\Z'),
                    'vlans' => [['id' => 10, 'name' => 'USERS']],
                    'interfaces' => [
                        ['name' => 'e0/0', 'kind' => 'physical', 'port' => ['adapter' => 0, 'port' => 0],
                            'admin' => 'up', 'oper' => 'up', 'switchport' => ['mode' => 'access', 'vlan' => 10]],
                    ],
                ],
                self::NODE_VNC => [
                    'name' => 'PC1',
                    'status' => 'started',
                    'error' => ['code' => 'unsupported', 'message' => 'No guest agent'],
                ],
            ],
        ];
    }

    /**
     * Requests to a path, in order.
     *
     * @param string $method HTTP method
     * @param string $path path without the query
     * @return array
     */
    public function requests_to(string $method, string $path): array {
        return array_values(array_filter($this->requests, fn($r) => $r['method'] === $method && $r['path'] === $path));
    }

    /**
     * A session as the manager returns it.
     *
     * @param string $status session status
     * @return array
     */
    public static function session(string $status = 'running'): array {
        $session = [
            'schema' => 'gns3lab.session/1',
            'session_id' => 's-3f9a1c0d2b4e5f60',
            'lab_ref' => 'cisco-3x2',
            'status' => $status,
            'stage' => $status === 'running' ? 'ready' : ($status === 'starting' ? 'lab_wait' : $status),
            'status_message' => 'Лаба запущена',
            'error' => null,
            'started_at' => '2026-10-17T10:00:00Z',
            'expires_at' => '2026-10-17T11:30:00Z',
            'features' => $status === 'running' ? ['consoles', 'facts'] : [],
        ];
        if ($status === 'running') {
            $session['topology'] = [
                'name' => 'cisco-3x2-w17',
                'topology' => [
                    'nodes' => [
                        ['node_id' => self::NODE_TELNET, 'name' => 'SW1', 'properties' => new \stdClass()],
                        ['node_id' => self::NODE_VNC, 'name' => 'PC1', 'properties' => new \stdClass()],
                    ],
                    'links' => [],
                    'drawings' => [],
                ],
            ];
            $session['nodes'] = [
                self::NODE_TELNET => ['status' => 'started', 'console_type' => 'telnet'],
                self::NODE_VNC => ['status' => 'started', 'console_type' => 'vnc'],
            ];
        }
        return $session;
    }

    /**
     * Answers GET /api/v1/info like manager 0.3 (CONTRACTS §2.1).
     *
     * @param array $features features to announce
     * @param array $extra more fields of the answer
     */
    public function with_info(array $features = ['consoles', 'observer'], array $extra = []): void {
        $this->on('GET', '/api/v1/info', 200, [
            'schema' => 'gns3lab.info/1',
            'version' => '0.3.0',
            'session_schemas' => ['gns3lab.session/1'],
            'options' => ['max_duration'],
            'features' => $features,
        ] + $extra);
    }

    /**
     * Answers like manager 0.5 that takes labs from Moodle (CONTRACTS §2.6): info, images and validate.
     *
     * @param string $labdef hash that validate returns
     */
    public function with_lab_definitions(string $labdef = 'sha256:aaaa'): void {
        $this->with_info(['consoles', 'observer', 'images', 'lab_definitions'], [
            'lab_formats' => ['gns3lab.lab/1'],
            'limits' => ['max_nodes' => 30, 'max_memory_mb' => 16384, 'max_cpus' => 8, 'images' => '*'],
        ]);
        $this->on('GET', '/api/v1/images', 200, ['images' => [self::image()]]);
        $this->on('POST', '/api/v1/labs/validate', 200, [
            'lab_def' => $labdef,
            'resources' => ['cores' => 2, 'memory_mb' => 3072],
            'warnings' => [],
        ]);
    }

    /**
     * Answers like manager 0.10 with the lab constructor (CONTRACTS §2.6): lab/2, preconfig of the images, warnings.
     *
     * @param string $labdef hash that validate returns
     */
    public function with_lab_constructor(string $labdef = 'sha256:bbbb'): void {
        $this->with_info(['consoles', 'observer', 'images', 'lab_definitions', 'lab_constructor'], [
            'lab_formats' => ['gns3lab.lab/1', 'gns3lab.lab/2'],
            'limits' => ['max_nodes' => 30, 'max_memory_mb' => 16384, 'max_cpus' => 8, 'images' => '*'],
        ]);
        $this->on('GET', '/api/v1/images', 200, ['images' => [self::image(), self::host_image()]]);
        $this->on('POST', '/api/v1/labs/validate', 200, [
            'lab_def' => $labdef,
            'resources' => ['cores' => 2, 'memory_mb' => 3072],
            'warnings' => [['path' => 'segments[0].gateway', 'code' => 'gateway_not_configured', 'message' => 'by the student']],
        ]);
    }

    /**
     * A Linux host image whose network the lab constructor can set up.
     *
     * @return array
     */
    public static function host_image(): array {
        return [
            'id' => 'debian12',
            'title' => 'Debian 12',
            'kind' => 'qemu',
            'category' => 'host',
            'symbol' => ':/symbols/computer.svg',
            'console_type' => 'telnet',
            'ram_mb' => ['default' => 768, 'min' => 512, 'max' => 4096],
            'cpus' => ['default' => 1, 'min' => 1, 'max' => 2],
            'adapters' => ['default' => 1, 'min' => 1, 'max' => 4],
            'port_name' => 'Ethernet{adapter}',
            'ports_per_adapter' => 1,
            'preconfig' => true,
        ];
    }

    /**
     * A lab definition of a router and two hosts with one segment, as the editor sends it (gns3lab.lab/2).
     *
     * @return array
     */
    public static function lab_with_segments(): array {
        return [
            'format' => 'gns3lab.lab/2',
            'nodes' => [
                ['name' => 'R1', 'image' => 'cisco-iou-l3', 'x' => 0, 'y' => 0, 'adapters' => 1],
                ['name' => 'PC1', 'image' => 'debian12', 'x' => -100, 'y' => 100, 'adapters' => 1],
                ['name' => 'PC2', 'image' => 'debian12', 'x' => 100, 'y' => 100, 'adapters' => 1],
            ],
            'links' => [
                ['a' => ['node' => 'R1', 'adapter' => 0, 'port' => 0], 'b' => ['node' => 'PC1', 'adapter' => 0, 'port' => 0]],
            ],
            'segments' => [[
                'name' => 'LAN1',
                'vlan' => ['fixed' => 10],
                'subnet' => ['fixed' => '192.168.10.0/24'],
                'gateway' => ['node' => 'R1', 'adapter' => 0, 'port' => 0],
                'members' => [
                    ['node' => 'PC1', 'adapter' => 0, 'port' => 0],
                    ['node' => 'PC2', 'adapter' => 0, 'port' => 0, 'host' => 20],
                ],
            ]],
        ];
    }

    /**
     * An image of the catalog: Cisco IOU L3.
     *
     * @return array
     */
    public static function image(): array {
        return [
            'id' => 'cisco-iou-l3',
            'title' => 'Cisco IOU L3',
            'kind' => 'iou',
            'category' => 'router',
            'symbol' => ':/symbols/classic/router.svg',
            'console_type' => 'telnet',
            'ram_mb' => ['default' => 1024, 'min' => 512, 'max' => 2048],
            'cpus' => null,
            'adapters' => ['default' => 2, 'min' => 1, 'max' => 4],
            'port_name' => 'Ethernet{adapter}/{port}',
            'ports_per_adapter' => 4,
        ];
    }

    /**
     * A lab definition of two routers and a link, as the editor sends it.
     *
     * @return array
     */
    public static function lab(): array {
        return [
            'format' => 'gns3lab.lab/1',
            'nodes' => [
                ['name' => 'R1', 'image' => 'cisco-iou-l3', 'x' => -100, 'y' => 0, 'adapters' => 2],
                ['name' => 'R2', 'image' => 'cisco-iou-l3', 'x' => 100, 'y' => 0, 'adapters' => 2, 'ram_mb' => 512],
            ],
            'links' => [
                ['a' => ['node' => 'R1', 'adapter' => 0, 'port' => 1], 'b' => ['node' => 'R2', 'adapter' => 1, 'port' => 0]],
            ],
        ];
    }

    /**
     * Answers one request.
     *
     * @param RequestInterface $request request
     * @return Response
     */
    protected function handle(RequestInterface $request): Response {
        $raw = (string) $request->getBody();
        $this->requests[] = [
            'method' => $request->getMethod(),
            'path' => $request->getUri()->getPath(),
            'query' => $request->getUri()->getQuery(),
            'headers' => array_map(fn($values) => implode(', ', $values), $request->getHeaders()),
            'body' => $raw === '' ? null : json_decode($raw, true),
        ];
        // A route may name the query too ("GET /api/v1/sessions?instance_id=7"): it wins over the one for the path.
        $route = $this->routes[$request->getMethod() . ' ' . $request->getUri()->getPath() . '?' . $request->getUri()->getQuery()]
            ?? $this->routes[$request->getMethod() . ' ' . $request->getUri()->getPath()]
            ?? null;
        if ($route === null) {
            return new Response(
                404,
                ['Content-Type' => 'application/json'],
                json_encode(['error' => 'not_found', 'message' => 'No route in fake_manager'])
            );
        }
        [$status, $body] = $route;
        parse_str($request->getUri()->getQuery(), $query);
        if (str_ends_with($request->getUri()->getPath(), '/state') && is_array($body) && isset($query['since_rev'])
                && (int) $query['since_rev'] === ($body['rev'] ?? null)) {
            return new Response(304);
        }
        return new Response($status, ['Content-Type' => 'application/json'], is_string($body) ? $body : json_encode($body));
    }
}
