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
 * Tests of deleting student work on the lab manager (manager CONTRACTS §2.10).
 *
 * @package    mod_gns3lab
 * @category   test
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
#[\PHPUnit\Framework\Attributes\CoversClass(\mod_gns3lab\local\purges::class)]
final class purges_test extends \advanced_testcase {
    /** @var fake_manager answers of the lab manager */
    protected fake_manager $fake;

    /** @var manager_client the client wired to the fake */
    protected manager_client $client;

    protected function setUp(): void {
        parent::setUp();
        require_once(__DIR__ . '/../classes/fake_manager.php');
        $this->resetAfterTest();
        $this->fake = new fake_manager();
        $this->client = $this->fake->install();
        $this->fake->with_info(['consoles', 'observer', 'purge']);
        $this->fake->on('POST', '/api/v1/purges', 200, ['workspaces' => 1, 'pending' => 0]);
    }

    /**
     * Bodies of the purge requests the fake received.
     *
     * @return array[]
     */
    protected function purge_bodies(): array {
        return array_values(array_map(fn($r) => $r['body'], array_filter(
            $this->fake->requests,
            fn($r) => $r['method'] === 'POST' && $r['path'] === '/api/v1/purges'
        )));
    }

    public function test_deleted_activity_is_queued_then_delivered_once(): void {
        global $DB;

        $course = $this->getDataGenerator()->create_course();
        $lab = $this->getDataGenerator()->create_module('gns3lab', ['course' => $course->id]);
        course_delete_module($lab->cmid);
        $this->assertEquals(1, $DB->count_records(purges::TABLE, ['instanceid' => $lab->id, 'userid' => 0]));
        purges::queue((int) $lab->id);
        $this->assertEquals(1, $DB->count_records(purges::TABLE));

        $this->assertSame(1, purges::send_pending($this->client, time()));
        $this->assertSame([['instance_id' => (int) $lab->id]], $this->purge_bodies());
        $this->assertEquals(0, $DB->count_records(purges::TABLE));
    }

    public function test_failed_delivery_waits_longer_and_an_old_manager_keeps_the_request(): void {
        global $DB;

        purges::queue(7, 42);
        $this->fake->on('POST', '/api/v1/purges', 503, ['error' => 'storage_unavailable', 'message' => 'down']);
        $now = time();
        $this->assertSame(0, purges::send_pending($this->client, $now));
        $row = $DB->get_record(purges::TABLE, ['instanceid' => 7]);
        $this->assertEquals(1, $row->attempts);
        $this->assertSame('storage_unavailable', $row->lasterror);
        $this->assertEquals($now + purges::RETRY_MIN, $row->nextattempt);
        $this->assertSame(0, purges::send_pending($this->client, $now), 'Not due yet');
        $this->assertCount(1, $this->purge_bodies());

        \cache::make('mod_gns3lab', 'info')->purge();
        $this->fake->with_info(['consoles', 'observer']);
        $this->assertSame(0, purges::send_pending($this->client, $now + 3600));
        $this->assertSame('nopurge', $DB->get_field(purges::TABLE, 'lasterror', ['instanceid' => 7]));
        $this->assertCount(1, $this->purge_bodies());
    }

    public function test_nothing_is_queued_without_a_manager(): void {
        global $DB;

        set_config('managerurl', '', 'gns3lab');
        purges::queue(7);
        $this->assertEquals(0, $DB->count_records(purges::TABLE));
    }

    public function test_wipe_refuses_a_running_lab_and_wipes_a_stopped_one(): void {
        global $DB;

        $course = $this->getDataGenerator()->create_course();
        $lab = $this->getDataGenerator()->create_module('gns3lab', ['course' => $course->id]);
        $DB->insert_record('gns3lab_reveal', (object) [
            'gns3labid' => $lab->id, 'userid' => 42, 'nodeid' => 'n1', 'kind' => 'node', 'item' => '', 'timecreated' => time(),
        ]);
        $this->fake->on('GET', '/api/v1/sessions', 200, fake_manager::session('running'));
        try {
            purges::wipe($lab, 42, $this->client);
            $this->fail('A running lab must not be wiped');
        } catch (\moodle_exception $e) {
            $this->assertSame('wipestopfirst', $e->errorcode);
        }
        $this->assertSame([], $this->purge_bodies());

        $this->fake->on('GET', '/api/v1/sessions', 200, fake_manager::session('stopped'));
        $result = purges::wipe($lab, 42, $this->client);
        $this->assertSame(0, $result->pending);
        $this->assertSame([['instance_id' => (int) $lab->id, 'user_id' => 42]], $this->purge_bodies());
        $this->assertEquals(0, $DB->count_records('gns3lab_reveal', ['gns3labid' => $lab->id, 'userid' => 42]));
    }

    public function test_reconcile_sends_every_activity_and_never_an_empty_list(): void {
        $this->fake->on('POST', '/api/v1/purges/reconcile', 200, ['workspaces' => 0, 'pending' => 0]);
        $this->assertNull(purges::reconcile($this->client));
        $course = $this->getDataGenerator()->create_course();
        $a = $this->getDataGenerator()->create_module('gns3lab', ['course' => $course->id]);
        $b = $this->getDataGenerator()->create_module('gns3lab', ['course' => $course->id]);
        purges::reconcile($this->client);
        $last = end($this->fake->requests);
        $this->assertSame('/api/v1/purges/reconcile', $last['path']);
        $ids = $last['body']['instance_ids'];
        sort($ids);
        $this->assertSame([(int) $a->id, (int) $b->id], $ids);
    }

    public function test_deleted_user_is_queued(): void {
        global $DB;

        $user = $this->getDataGenerator()->create_user();
        delete_user($user);
        $this->assertTrue($DB->record_exists(purges::TABLE, ['instanceid' => 0, 'userid' => $user->id, 'librarylabid' => 0]));
    }
}
