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

use mod_gns3lab\tests\fake_manager;

/**
 * Tests of the lab manager client and of the session export.
 *
 * @package    mod_gns3lab
 * @category   test
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
#[\PHPUnit\Framework\Attributes\CoversClass(\mod_gns3lab\local\manager_client::class)]
#[\PHPUnit\Framework\Attributes\CoversClass(\mod_gns3lab\local\manager_exception::class)]
#[\PHPUnit\Framework\Attributes\CoversClass(\mod_gns3lab\local\session_data::class)]
final class manager_client_test extends \advanced_testcase {
    /** @var fake_manager */
    protected fake_manager $manager;

    /** @var manager_client */
    protected manager_client $client;

    protected function setUp(): void {
        global $CFG;
        parent::setUp();
        $this->resetAfterTest();
        $CFG->wwwroot = 'https://moodle.example.org';
        $this->manager = new fake_manager();
        $this->client = $this->manager->install();
    }

    public function test_create_session_sends_contract_and_site_headers(): void {
        $this->manager->on('POST', '/api/v1/sessions', 201, fake_manager::session('queued'));

        $session = $this->client->create_session(42, 7, 3, 'cisco-3x2');

        $this->assertSame('queued', $session->status);
        [$request] = $this->manager->requests_to('POST', '/api/v1/sessions');
        $this->assertSame('Bearer site-secret', $request['headers']['Authorization']);
        $this->assertSame('https://moodle.example.org', $request['headers']['X-Moodle-Site']);
        $this->assertSame(['user_id' => 42, 'instance_id' => 7, 'lab_ref' => 'cisco-3x2', 'course_id' => 3], $request['body']);
    }

    public function test_find_session_returns_null_when_the_user_never_had_one(): void {
        $this->manager->on('GET', '/api/v1/sessions', 404, ['error' => 'session_not_found', 'message' => 'No session']);

        $this->assertNull($this->client->find_session(42, 7));
        [$request] = $this->manager->requests_to('GET', '/api/v1/sessions');
        $this->assertSame('user_id=42&instance_id=7', $request['query']);
    }

    public function test_manager_error_code_selects_the_message(): void {
        $this->manager->on(
            'POST',
            '/api/v1/sessions',
            409,
            ['error' => 'session_conflict', 'message' => 'У студента уже запущена другая лаба']
        );

        try {
            $this->client->create_session(42, 7, 3, 'cisco-3x2');
            $this->fail('No exception');
        } catch (manager_exception $e) {
            $this->assertSame('session_conflict', $e->remotecode);
            $this->assertSame(409, $e->httpstatus);
            $this->assertSame('managererror_session_conflict', $e->errorcode);
            $this->assertStringContainsString('другая лаба', $e->debuginfo);
        }
    }

    public function test_unknown_error_code_and_broken_json(): void {
        $this->manager->on('GET', '/api/v1/labs', 503, ['error' => 'catalog_unavailable', 'message' => 'x']);
        try {
            $this->client->list_labs();
            $this->fail('No exception');
        } catch (manager_exception $e) {
            $this->assertSame('managererror', $e->errorcode);
        }

        $this->manager->on('GET', '/api/v1/labs', 200, 'not json');
        $this->expectException(manager_exception::class);
        $this->client->list_labs();
    }

    public function test_not_configured(): void {
        set_config('managersecret', '', 'gns3lab');
        $this->assertFalse(manager_client::is_configured());

        $this->expectExceptionObject(new manager_exception('notconfigured'));
        (new manager_client())->list_labs();
    }

    public function test_session_export_keeps_topology_objects(): void {
        $this->manager->on('GET', '/api/v1/sessions', 200, fake_manager::session());

        $exported = session_data::export($this->client->find_session(42, 7));

        $this->assertSame('running', $exported['status']);
        $this->assertSame(strtotime('2026-10-17T11:30:00Z'), $exported['expiresat']);
        $this->assertStringContainsString('"properties":{}', $exported['topology']);
        $this->assertCount(2, $exported['nodes']);
        $this->assertSame(
            ['nodeid' => fake_manager::NODE_TELNET, 'status' => 'started', 'consoletype' => 'telnet'],
            $exported['nodes'][0]
        );
    }

    public function test_session_export_hides_topology_unless_running(): void {
        $exported = session_data::export(json_decode(json_encode(fake_manager::session('stopping'))));

        $this->assertSame('', $exported['topology']);
        $this->assertSame([], $exported['nodes']);
    }
}
