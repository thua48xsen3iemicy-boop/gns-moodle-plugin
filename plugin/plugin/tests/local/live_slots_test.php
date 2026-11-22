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
 * Tests of the busy slots page data (manager CONTRACTS §2.13).
 *
 * @package    mod_gns3lab
 * @category   test
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
#[\PHPUnit\Framework\Attributes\CoversClass(\mod_gns3lab\local\live_slots::class)]
final class live_slots_test extends \advanced_testcase {
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
        $this->fake->with_info(['consoles', 'observer', live_slots::FEATURE]);
    }

    /**
     * A live session as the manager lists it.
     *
     * @param array $fields fields to change
     * @return array
     */
    protected static function session(array $fields): array {
        return $fields + [
            'schema' => 'gns3lab.session/1', 'session_id' => 's-0000000000000001', 'lab_ref' => 'cisco-3x2',
            'lab_def' => null, 'status' => 'running', 'stage' => 'ready', 'status_message' => '', 'error' => null,
            'started_at' => '2026-11-22T09:10:00Z', 'expires_at' => '2026-11-22T10:40:00Z', 'slot_vmid' => 9012,
            'user_id' => 0, 'instance_id' => null, 'course_id' => null, 'library_lab_id' => null,
            'created_at' => '2026-11-22T09:08:41Z',
        ];
    }

    public function test_rows_name_user_course_activity_and_lab(): void {
        global $DB;

        $course = $this->getDataGenerator()->create_course(['shortname' => 'NET-101']);
        $lab = $this->getDataGenerator()->create_module('gns3lab', ['course' => $course->id, 'name' => 'OSPF lab',
            'labref' => 'cisco-3x2']);
        $student = $this->getDataGenerator()->create_user(['username' => 'ivanov', 'firstname' => 'Ivan',
            'lastname' => 'Ivanov']);
        $author = $this->getDataGenerator()->create_user(['username' => 'author']);
        $librarylab = $DB->insert_record('gns3lab_labs', ['name' => 'Draft lab', 'timecreated' => time(),
            'timemodified' => time()]);
        $this->fake->on('GET', '/api/v1/live-sessions', 200, [
            'schema' => 'gns3lab.live_sessions/1',
            'slots' => ['total' => 5, 'free' => 2, 'unavailable' => 1],
            'sessions' => [
                self::session(['user_id' => (int) $student->id, 'instance_id' => (int) $lab->id,
                    'course_id' => (int) $course->id]),
                self::session(['session_id' => 's-0000000000000002', 'slot_vmid' => 9013, 'mode' => 'authoring',
                    'user_id' => (int) $author->id, 'library_lab_id' => $librarylab, 'lab_ref' => null]),
                self::session(['session_id' => 's-0000000000000003', 'status' => 'queued', 'stage' => 'wait_slot',
                    'started_at' => null, 'expires_at' => null, 'slot_vmid' => null, 'user_id' => 999999,
                    'instance_id' => 424242, 'course_id' => (int) $course->id]),
            ],
        ]);

        ['slots' => $slots, 'rows' => $rows] = live_slots::load($this->client);
        $this->assertSame(5, $slots->total);
        $this->assertCount(3, $rows);

        [$own, $authoring, $queued] = $rows;
        $this->assertSame(9012, $own['slot']);
        $this->assertSame('ivanov', $own['username']);
        $this->assertSame('Ivan Ivanov', $own['fullname']);
        $this->assertSame('NET-101', $own['course']);
        $this->assertSame('OSPF lab', $own['activity']);
        $this->assertSame('cisco-3x2', $own['lab']);
        $this->assertStringContainsString('/mod/gns3lab/lab.php?id=' . $lab->cmid, $own['observeurl']);
        $this->assertSame(get_string('stage_ready', 'gns3lab'), $own['status']);

        $this->assertTrue($authoring['authoring']);
        $this->assertSame('author', $authoring['username']);
        $this->assertSame('Draft lab', $authoring['lab']);
        $this->assertNull($authoring['observeurl']);

        $this->assertNull($queued['slot']);
        $this->assertSame('', $queued['username']);
        $this->assertSame(get_string('liveslotsunknownuser', 'gns3lab'), $queued['fullname']);
        $this->assertSame(get_string('liveslotsdeletedactivity', 'gns3lab', 424242), $queued['activity']);
        $this->assertSame('NET-101', $queued['course']);
        $this->assertNull($queued['started']);
        $this->assertNotNull($queued['created']);
    }

    public function test_only_a_live_session_is_stopped_and_the_stopper_is_named(): void {
        $this->fake->on('GET', '/api/v1/live-sessions', 200, [
            'schema' => 'gns3lab.live_sessions/1',
            'slots' => ['total' => 1, 'free' => 0, 'unavailable' => 0],
            'sessions' => [self::session(['user_id' => 7])],
        ]);
        $this->assertTrue(live_slots::is_live($this->client, 's-0000000000000001'));
        $this->assertFalse(live_slots::is_live($this->client, 's-ffffffffffffffff'));

        $this->fake->on('POST', '/api/v1/sessions/s-0000000000000001/stop', 200,
            self::session(['user_id' => 7, 'status' => 'stopping', 'stage' => 'lab_stop']));
        $this->client->stop_session('s-0000000000000001', 2);
        $last = end($this->fake->requests);
        $this->assertSame(['requested_by_user_id' => 2], (array) $last['body']);
    }

    public function test_bad_answer_is_refused(): void {
        $this->fake->on('GET', '/api/v1/live-sessions', 200, ['sessions' => []]);
        $this->expectException(manager_exception::class);
        $this->client->list_live_sessions();
    }
}
