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
use mod_gns3lab\tests\fake_manager;

/**
 * Tests of mod_gns3lab_get_state, mod_gns3lab_start_session and mod_gns3lab_stop_session.
 *
 * @package    mod_gns3lab
 * @category   test
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
#[\PHPUnit\Framework\Attributes\CoversClass(\mod_gns3lab\external\get_state::class)]
#[\PHPUnit\Framework\Attributes\CoversClass(\mod_gns3lab\external\start_session::class)]
#[\PHPUnit\Framework\Attributes\CoversClass(\mod_gns3lab\external\stop_session::class)]
final class session_test extends \advanced_testcase {
    /** @var fake_manager */
    protected fake_manager $manager;

    /** @var \stdClass course */
    protected \stdClass $course;

    /** @var \stdClass activity with cmid */
    protected \stdClass $lab;

    /** @var \stdClass student */
    protected \stdClass $student;

    protected function setUp(): void {
        parent::setUp();
        $this->resetAfterTest();
        $gen = $this->getDataGenerator();
        $this->course = $gen->create_course();
        $this->lab = $gen->create_module('gns3lab', ['course' => $this->course->id]);
        $this->student = $gen->create_and_enrol($this->course, 'student');
        $this->manager = new fake_manager();
        $this->manager->install();
    }

    public function test_state_without_a_session(): void {
        $this->setUser($this->student);

        $state = external_api::clean_returnvalue(get_state::execute_returns(), get_state::execute($this->lab->cmid));

        $this->assertSame(
            ['canuse' => true, 'readonly' => false, 'managerconfigured' => true, 'labready' => true, 'resume' => false],
            $state
        );
    }

    public function test_state_with_a_running_session(): void {
        $this->manager->on('GET', '/api/v1/sessions', 200, fake_manager::session());
        $this->setUser($this->student);

        $state = external_api::clean_returnvalue(get_state::execute_returns(), get_state::execute($this->lab->cmid));

        $this->assertSame('running', $state['session']['status']);
        $this->assertSame('s-3f9a1c0d2b4e5f60', $state['session']['sessionid']);
        $this->assertSame('cisco-3x2-w17', json_decode($state['session']['topology'])->name);
    }

    public function test_viewer_only_gets_the_flags(): void {
        global $DB;
        $roleid = $DB->get_field('role', 'id', ['shortname' => 'student'], MUST_EXIST);
        assign_capability('mod/gns3lab:use', CAP_PROHIBIT, $roleid, \context_module::instance($this->lab->cmid));
        $this->setUser($this->student);

        $state = external_api::clean_returnvalue(get_state::execute_returns(), get_state::execute($this->lab->cmid));

        $this->assertFalse($state['canuse']);
        $this->assertArrayNotHasKey('session', $state);
        $this->assertSame([], $this->manager->requests);
    }

    public function test_start_sends_the_activity_lab(): void {
        $this->manager->on('POST', '/api/v1/sessions', 201, fake_manager::session('queued'));
        $this->setUser($this->student);

        $session = external_api::clean_returnvalue(start_session::execute_returns(), start_session::execute($this->lab->cmid));

        $this->assertSame('queued', $session['status']);
        [$request] = $this->manager->requests_to('POST', '/api/v1/sessions');
        $this->assertSame([
            'user_id' => (int) $this->student->id,
            'instance_id' => (int) $this->lab->id,
            'lab_ref' => 'cisco-3x2',
            'course_id' => (int) $this->course->id,
        ], $request['body']);
    }

    public function test_start_reports_a_conflict(): void {
        $this->manager->on('POST', '/api/v1/sessions', 409, ['error' => 'session_conflict', 'message' => 'Другая лаба']);
        $this->setUser($this->student);

        $this->expectExceptionMessage(get_string('managererror_session_conflict', 'gns3lab'));
        start_session::execute($this->lab->cmid);
    }

    public function test_stop_finds_the_session_by_user_and_activity(): void {
        $this->manager->on('GET', '/api/v1/sessions', 200, fake_manager::session());
        $this->manager->on('POST', '/api/v1/sessions/s-3f9a1c0d2b4e5f60/stop', 200, fake_manager::session('stopping'));
        $this->setUser($this->student);

        $session = external_api::clean_returnvalue(stop_session::execute_returns(), stop_session::execute($this->lab->cmid));

        $this->assertSame('stopping', $session['status']);
        $this->assertCount(1, $this->manager->requests_to('POST', '/api/v1/sessions/s-3f9a1c0d2b4e5f60/stop'));
    }

    public function test_stop_without_a_session(): void {
        $this->setUser($this->student);

        $this->expectExceptionMessage(get_string('nosession', 'gns3lab'));
        stop_session::execute($this->lab->cmid);
    }

    public function test_state_tells_whether_work_is_kept(): void {
        $this->manager->with_info(['consoles', 'observer', 'resume']);
        $this->setUser($this->student);

        $state = external_api::clean_returnvalue(get_state::execute_returns(), get_state::execute($this->lab->cmid));

        $this->assertTrue($state['resume']);
    }

    public function test_start_passes_the_activity_limit(): void {
        global $DB;
        $DB->set_field('gns3lab', 'maxduration', 45, ['id' => $this->lab->id]);
        $this->manager->with_info();
        $this->manager->on('POST', '/api/v1/sessions', 201, fake_manager::session('queued'));
        $this->setUser($this->student);

        start_session::execute($this->lab->cmid);

        [$request] = $this->manager->requests_to('POST', '/api/v1/sessions');
        $this->assertSame(['max_duration' => 45], $request['body']['options']);
    }

    public function test_conflict_links_to_the_other_activity(): void {
        $other = $this->getDataGenerator()->create_module('gns3lab', ['course' => $this->course->id, 'name' => 'OSPF']);
        $this->manager->on('POST', '/api/v1/sessions', 409, [
            'error' => 'session_conflict',
            'message' => 'Другая лаба',
            'conflict' => ['instance_id' => (int) $other->id, 'course_id' => (int) $this->course->id,
                'lab_ref' => 'ospf', 'status' => 'running'],
        ]);
        $this->setUser($this->student);

        try {
            start_session::execute($this->lab->cmid);
            $this->fail('No exception');
        } catch (\moodle_exception $e) {
            $this->assertSame('sessionconflictat', $e->errorcode);
            $this->assertStringContainsString('OSPF', $e->getMessage());
            $this->assertSame((new \core\url('/mod/gns3lab/view.php', ['id' => $other->cmid]))->out(false), $e->link);
        }
    }

    public function test_conflict_in_an_activity_the_user_cannot_open(): void {
        $course = $this->getDataGenerator()->create_course();
        $hidden = $this->getDataGenerator()->create_module('gns3lab', ['course' => $course->id]);
        $this->manager->on('POST', '/api/v1/sessions', 409, [
            'error' => 'session_conflict',
            'message' => 'Другая лаба',
            'conflict' => ['instance_id' => (int) $hidden->id, 'course_id' => null, 'lab_ref' => 'x', 'status' => 'running'],
        ]);
        $this->setUser($this->student);

        $this->expectExceptionMessage(get_string('managererror_session_conflict', 'gns3lab'));
        start_session::execute($this->lab->cmid);
    }
}
