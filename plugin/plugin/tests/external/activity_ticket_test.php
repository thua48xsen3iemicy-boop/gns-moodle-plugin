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
 * Tests of mod_gns3lab_get_activity_ticket: only an observer follows the consoles of a student (manager CONTRACTS §6.3).
 *
 * @package    mod_gns3lab
 * @category   test
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
#[\PHPUnit\Framework\Attributes\CoversClass(\mod_gns3lab\external\get_activity_ticket::class)]
final class activity_ticket_test extends \advanced_testcase {
    /** Ticket path of the running session. */
    private const TICKETS = '/api/v1/sessions/s-3f9a1c0d2b4e5f60/activity-tickets';

    /** @var fake_manager */
    protected fake_manager $manager;

    /** @var \stdClass course */
    protected \stdClass $course;

    /** @var \stdClass activity with cmid */
    protected \stdClass $lab;

    /** @var \stdClass student */
    protected \stdClass $student;

    /** @var \stdClass non-editing teacher */
    protected \stdClass $teacher;

    protected function setUp(): void {
        parent::setUp();
        $this->resetAfterTest();
        $gen = $this->getDataGenerator();
        $this->course = $gen->create_course(['groupmode' => SEPARATEGROUPS, 'groupmodeforce' => 1]);
        $this->lab = $gen->create_module('gns3lab', ['course' => $this->course->id]);
        $this->student = $gen->create_and_enrol($this->course, 'student');
        $this->teacher = $gen->create_and_enrol($this->course, 'teacher');
        $group = $gen->create_group(['courseid' => $this->course->id]);
        $gen->create_group_member(['groupid' => $group->id, 'userid' => $this->student->id]);
        $gen->create_group_member(['groupid' => $group->id, 'userid' => $this->teacher->id]);

        $this->manager = new fake_manager();
        $this->manager->install();
        $this->manager->with_info(['consoles', 'observer', 'console-activity']);
        $this->manager->on('GET', '/api/v1/sessions', 200, fake_manager::session());
        $this->manager->on(
            'POST',
            self::TICKETS,
            200,
            ['ticket' => 't-act', 'gateway_url' => 'wss://gw.example.org/', 'expires_in' => 30]
        );
    }

    /**
     * Calls the external function and cleans the result like the web service layer does.
     *
     * @param int $userid the observed student
     * @return array
     */
    protected function ticket(int $userid): array {
        $result = get_activity_ticket::execute($this->lab->cmid, $userid);
        return external_api::clean_returnvalue(get_activity_ticket::execute_returns(), $result);
    }

    public function test_teacher_gets_a_ticket_to_follow_a_student_in_own_group(): void {
        $this->setUser($this->teacher);

        $ticket = $this->ticket((int) $this->student->id);

        $this->assertSame(['ticket' => 't-act', 'gatewayurl' => 'wss://gw.example.org', 'expiresin' => 30], $ticket);
        [$find] = $this->manager->requests_to('GET', '/api/v1/sessions');
        $this->assertSame("user_id={$this->student->id}&instance_id={$this->lab->id}", $find['query']);
        [$request] = $this->manager->requests_to('POST', self::TICKETS);
        $this->assertSame(['issued_for_user_id' => (int) $this->teacher->id], $request['body']);
    }

    public function test_student_does_not_follow_own_consoles(): void {
        $this->setUser($this->student);

        foreach ([0, (int) $this->student->id] as $userid) {
            try {
                $this->ticket($userid);
                $this->fail('No exception');
            } catch (\moodle_exception $e) {
                $this->assertSame('consoleactivityobserver', $e->errorcode);
            }
        }
        $this->assertSame([], $this->manager->requests_to('POST', self::TICKETS));
    }

    public function test_student_cannot_follow_another_student(): void {
        $other = $this->getDataGenerator()->create_and_enrol($this->course, 'student');
        $this->setUser($other);

        $this->expectException(\required_capability_exception::class);
        $this->ticket((int) $this->student->id);
    }

    public function test_teacher_outside_the_student_group_gets_nothing(): void {
        global $DB;
        $outsider = $this->getDataGenerator()->create_and_enrol($this->course, 'teacher');
        $roleid = $DB->get_field('role', 'id', ['shortname' => 'teacher'], MUST_EXIST);
        assign_capability('moodle/site:accessallgroups', CAP_PROHIBIT, $roleid, \context_module::instance($this->lab->cmid));
        $this->setUser($outsider);

        try {
            $this->ticket((int) $this->student->id);
            $this->fail('No exception');
        } catch (\moodle_exception $e) {
            $this->assertSame('cannotobserveuser', $e->errorcode);
        }
        $this->assertSame([], $this->manager->requests);
    }

    public function test_manager_without_the_feature_is_not_asked(): void {
        $this->manager->with_info(['consoles', 'observer']);
        $this->setUser($this->teacher);

        try {
            $this->ticket((int) $this->student->id);
            $this->fail('No exception');
        } catch (\moodle_exception $e) {
            $this->assertSame('consoleactivityunsupported', $e->errorcode);
        }
        $this->assertSame([], $this->manager->requests_to('POST', self::TICKETS));
    }

    public function test_stopped_lab_gets_no_ticket(): void {
        $this->manager->on('GET', '/api/v1/sessions', 200, fake_manager::session('stopped'));
        $this->setUser($this->teacher);

        try {
            $this->ticket((int) $this->student->id);
            $this->fail('No exception');
        } catch (\moodle_exception $e) {
            $this->assertSame('sessionnotrunning', $e->errorcode);
        }
        $this->assertSame([], $this->manager->requests_to('POST', self::TICKETS));
    }
}
