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

    public function test_options_and_client_only_when_info_allows(): void {
        global $CFG;
        $this->manager->with_info();
        $this->manager->on('POST', '/api/v1/sessions', 201, fake_manager::session('queued'));

        $this->client->create_session(42, 7, 3, 'cisco-3x2', 120);
        $this->client->create_session(42, 7, 3, 'cisco-3x2', 0);

        [$first, $second] = $this->manager->requests_to('POST', '/api/v1/sessions');
        $this->assertSame(['max_duration' => 120], $first['body']['options']);
        $this->assertSame((string) get_config('mod_gns3lab', 'version'), $first['body']['client']['plugin_version']);
        $this->assertSame($CFG->release, $first['body']['client']['moodle_release']);
        $this->assertArrayNotHasKey('options', $second['body'], '0 means the manager default: nothing to send');
        $this->assertCount(1, $this->manager->requests_to('GET', '/api/v1/info'), 'info is cached');
    }

    public function test_old_manager_without_info_gets_spec_fields_only(): void {
        $this->manager->on('POST', '/api/v1/sessions', 201, fake_manager::session('queued'));

        $this->client->create_session(42, 7, 3, 'cisco-3x2', 120);

        [$request] = $this->manager->requests_to('POST', '/api/v1/sessions');
        $this->assertSame(['user_id', 'instance_id', 'lab_ref', 'course_id'], array_keys($request['body']));
        $info = $this->client->get_info();
        $this->assertFalse($info->known);
        $this->assertSame([], $info->features);
    }

    public function test_unavailable_info_is_asked_again_soon(): void {
        $this->manager->on('GET', '/api/v1/info', 503, ['error' => 'storage_unavailable', 'message' => 'x']);
        $this->assertFalse($this->client->get_info()->known);

        $this->manager->with_info(['consoles', 'resume']);
        $this->assertFalse($this->client->get_info()->known, 'still cached');
        $cache = \cache::make('mod_gns3lab', 'info');
        $key = 'm' . md5('https://manager.test');
        $entry = $cache->get($key);
        $this->assertLessThanOrEqual(time() + manager_client::INFO_RETRY, $entry->until);
        $entry->until = time() - 1;
        $cache->set($key, $entry);

        $this->assertSame(['consoles', 'resume'], $this->client->get_info()->features);
    }

    public function test_session_export_of_new_fields(): void {
        $failed = fake_manager::session('failed');
        $failed['error'] = ['code' => 'SLOT_START_FAILED', 'retryable' => true];
        $exported = session_data::export(json_decode(json_encode($failed)));
        $this->assertSame('SLOT_START_FAILED', $exported['errorcode']);
        $this->assertTrue($exported['errorretryable']);
        $this->assertSame(-1, $exported['progress']);

        $starting = fake_manager::session('starting');
        $starting['progress'] = 40;
        $exported = session_data::export(json_decode(json_encode($starting)));
        $this->assertSame('lab_wait', $exported['stage']);
        $this->assertSame(40, $exported['progress']);
        $this->assertSame('', $exported['errorcode']);
        $this->assertFalse($exported['errorretryable']);

        $running = fake_manager::session();
        $running['nodes_stale'] = true;
        $this->assertTrue(session_data::export(json_decode(json_encode($running)))['nodesstale']);
        $this->assertFalse(session_data::export(json_decode(json_encode(fake_manager::session())))['nodesstale']);
    }

    public function test_unknown_error_code_is_not_retryable(): void {
        $failed = fake_manager::session('failed');
        $failed['error'] = ['code' => 'SOMETHING_NEW'];

        $exported = session_data::export(json_decode(json_encode($failed)));

        $this->assertSame('SOMETHING_NEW', $exported['errorcode']);
        $this->assertFalse($exported['errorretryable']);
    }

    public function test_activity_sessions_for_the_observer_list(): void {
        $this->manager->on('GET', '/api/v1/sessions', 200, [
            fake_manager::session('stopped') + ['user_id' => 42],
        ]);

        $sessions = $this->client->list_activity_sessions(7);

        $this->assertSame(42, $sessions[0]->user_id);
        [$request] = $this->manager->requests_to('GET', '/api/v1/sessions');
        $this->assertSame('instance_id=7', $request['query']);
    }
}
