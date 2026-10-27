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
#[\PHPUnit\Framework\Attributes\CoversClass(\mod_gns3lab\local\session_facts::class)]
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

    public function test_lab_definitions_need_the_format(): void {
        $this->manager->with_info(['consoles', 'images', 'lab_definitions']);
        $this->assertTrue($this->client->has_feature('lab_definitions'));
        $this->assertFalse($this->client->supports_lab_definitions());

        \cache::make('mod_gns3lab', 'info')->purge();
        $this->manager->with_lab_definitions();
        $this->assertTrue($this->client->supports_lab_definitions());
        $this->assertSame(30, $this->client->get_info()->limits->max_nodes);
        $this->assertSame('cisco-iou-l3', $this->client->get_images()[0]->id);
    }

    public function test_the_lab_constructor_needs_its_feature_and_format(): void {
        $this->manager->with_lab_definitions();
        $this->assertFalse($this->client->supports_lab_constructor());

        \cache::make('mod_gns3lab', 'info')->purge();
        $this->manager->with_info(['consoles', 'images', 'lab_definitions', 'lab_constructor'], [
            'lab_formats' => ['gns3lab.lab/1'],
        ]);
        $this->assertFalse($this->client->supports_lab_constructor());

        \cache::make('mod_gns3lab', 'info')->purge();
        $this->manager->with_lab_constructor();
        $this->assertTrue($this->client->supports_lab_constructor());
        $this->assertTrue($this->client->supports_lab_definitions());
        $this->assertTrue($this->client->get_images()[1]->preconfig);
    }

    public function test_session_facts_full_and_not_modified(): void {
        $this->manager->with_facts();

        $facts = $this->client->get_session_facts('s-3f9a1c0d2b4e5f60');

        $this->assertSame(5, $facts->rev);
        $this->assertInstanceOf(\stdClass::class, $facts->nodes->{fake_manager::NODE_TELNET});
        [$request] = $this->manager->requests_to('GET', fake_manager::STATE);
        $this->assertSame('', $request['query']);
        $this->assertSame('Bearer site-secret', $request['headers']['Authorization']);

        $this->assertNull($this->client->get_session_facts('s-3f9a1c0d2b4e5f60', 5));
        $this->assertSame('since_rev=5', $this->manager->requests_to('GET', fake_manager::STATE)[1]['query']);
        $this->assertSame(5, $this->client->get_session_facts('s-3f9a1c0d2b4e5f60', 4)->rev);
    }

    public function test_session_facts_without_data_keep_nodes_an_object(): void {
        $this->manager->on('GET', fake_manager::STATE, 200, '{"schema":"gns3lab.facts/1","rev":0,"ttl":90,"nodes":{}}');

        $facts = $this->client->get_session_facts('s-3f9a1c0d2b4e5f60');

        $this->assertSame(0, $facts->rev);
        $this->assertSame('{}', json_encode($facts->nodes));
    }

    public function test_session_facts_of_another_schema_are_refused(): void {
        $this->manager->with_facts(['schema' => 'gns3lab.facts/2'] + fake_manager::facts());

        try {
            $this->client->get_session_facts('s-3f9a1c0d2b4e5f60');
            $this->fail('No exception');
        } catch (manager_exception $e) {
            $this->assertSame('badresponse', $e->remotecode);
        }

        $this->manager->on('GET', fake_manager::STATE, 200, ['schema' => 'gns3lab.facts/1', 'rev' => 1, 'nodes' => []]);
        $this->expectException(manager_exception::class);
        $this->client->get_session_facts('s-3f9a1c0d2b4e5f60');
    }

    public function test_304_is_an_error_unless_expected(): void {
        $this->manager->on('GET', '/api/v1/sessions', 304, '');

        $this->expectException(manager_exception::class);
        $this->client->find_session(42, 7);
    }

    public function test_refresh_session_facts(): void {
        $this->manager->with_facts();

        $this->client->refresh_session_facts('s-3f9a1c0d2b4e5f60');

        $this->assertCount(1, $this->manager->requests_to('POST', fake_manager::STATE . '/refresh'));

        $this->manager->on(
            'POST',
            fake_manager::STATE . '/refresh',
            409,
            ['error' => 'session_not_running', 'message' => 'Session is not running']
        );
        try {
            $this->client->refresh_session_facts('s-3f9a1c0d2b4e5f60');
            $this->fail('No exception');
        } catch (manager_exception $e) {
            $this->assertSame('managererror_session_not_running', $e->errorcode);
            $this->assertSame(409, $e->httpstatus);
        }
    }

    public function test_session_export_of_features(): void {
        $running = json_decode(json_encode(fake_manager::session()));
        $this->assertSame(['consoles', 'facts'], session_data::export($running)['features']);

        $old = fake_manager::session();
        unset($old['features']);
        $session = json_decode(json_encode($old));
        $this->assertSame([], session_data::export($session)['features']);
        $this->assertFalse(session_data::has_feature($session, 'facts'));
    }

    public function test_facts_cache_asks_the_manager_only_when_needed(): void {
        $this->manager->with_facts();
        $sessionid = 's-3f9a1c0d2b4e5f60';

        $first = session_facts::get($this->client, $sessionid);
        $second = session_facts::get($this->client, $sessionid);

        $this->assertSame(5, $first->rev);
        $this->assertSame($first->doc, $second->doc);
        $this->assertCount(1, $this->manager->requests_to('GET', fake_manager::STATE), 'fresh entry served from the cache');

        // An older entry with a recent snapshot is checked with since_rev; 304 only extends it.
        $this->age_facts_entry($sessionid, session_facts::FRESH + 1, 0);
        $third = session_facts::get($this->client, $sessionid);
        $requests = $this->manager->requests_to('GET', fake_manager::STATE);
        $this->assertSame('since_rev=5', $requests[1]['query']);
        $this->assertSame($first->doc, $third->doc);
        $this->assertGreaterThanOrEqual(time() - 1, $third->fetchedat);

        // Half of the ttl later the snapshot is read in full: generated_at moves without a new rev.
        $this->age_facts_entry($sessionid, session_facts::FRESH + 1, 60);
        session_facts::get($this->client, $sessionid);
        $this->assertSame('', $this->manager->requests_to('GET', fake_manager::STATE)[2]['query']);

        session_facts::forget($sessionid);
        session_facts::get($this->client, $sessionid);
        $this->assertCount(4, $this->manager->requests_to('GET', fake_manager::STATE));
    }

    /**
     * Makes the cached facts of a session look older.
     *
     * @param string $sessionid session id
     * @param int $fetched seconds since the entry was read
     * @param int $generated seconds since the snapshot was made
     */
    protected function age_facts_entry(string $sessionid, int $fetched, int $generated): void {
        $cache = \cache::make('mod_gns3lab', 'facts');
        $key = 'f' . sha1("https://manager.test\n" . $sessionid);
        $entry = $cache->get($key);
        $this->assertNotFalse($entry);
        $entry->fetchedat = time() - $fetched;
        $entry->generatedat = time() - $generated;
        $cache->set($key, $entry);
    }
}
