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
 * Tests of mod_gns3lab_get_console_ticket: who gets a ticket to which console (SPEC §6, stage 3).
 *
 * @package    mod_gns3lab
 * @category   test
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
#[\PHPUnit\Framework\Attributes\CoversClass(\mod_gns3lab\external\get_console_ticket::class)]
#[\PHPUnit\Framework\Attributes\CoversClass(\mod_gns3lab\local\lab_access::class)]
final class console_ticket_test extends \advanced_testcase {
    /** Ticket answer path of the running session. */
    private const TICKETS = '/api/v1/sessions/s-3f9a1c0d2b4e5f60/console-tickets';

    /** @var fake_manager */
    protected fake_manager $manager;

    /** @var \stdClass course */
    protected \stdClass $course;

    /** @var \stdClass activity with cmid */
    protected \stdClass $lab;

    /** @var \stdClass students and teachers */
    protected \stdClass $student;

    /** @var \stdClass another student */
    protected \stdClass $other;

    /** @var \stdClass non-editing teacher */
    protected \stdClass $teacher;

    protected function setUp(): void {
        parent::setUp();
        $this->resetAfterTest();
        $gen = $this->getDataGenerator();
        $this->course = $gen->create_course(['groupmode' => SEPARATEGROUPS, 'groupmodeforce' => 1]);
        $this->lab = $gen->create_module('gns3lab', ['course' => $this->course->id]);
        $this->student = $gen->create_and_enrol($this->course, 'student');
        $this->other = $gen->create_and_enrol($this->course, 'student');
        $this->teacher = $gen->create_and_enrol($this->course, 'teacher');

        $this->manager = new fake_manager();
        $this->manager->install();
        $this->manager->on('GET', '/api/v1/sessions', 200, fake_manager::session());
        $this->manager->on(
            'POST',
            self::TICKETS,
            200,
            ['ticket' => 't-123', 'gateway_url' => 'wss://gw.example.org/', 'expires_in' => 30]
        );
    }

    /**
     * Calls the external function and cleans the result like the web service layer does.
     *
     * @param string $nodeid node id
     * @param int $userid whose lab
     * @return array
     */
    protected function ticket(string $nodeid = fake_manager::NODE_TELNET, int $userid = 0): array {
        $result = get_console_ticket::execute($this->lab->cmid, $nodeid, $userid);
        return external_api::clean_returnvalue(get_console_ticket::execute_returns(), $result);
    }

    public function test_student_gets_a_writable_ticket_to_own_lab(): void {
        $this->setUser($this->student);

        $ticket = $this->ticket();

        $this->assertSame(
            ['ticket' => 't-123', 'gatewayurl' => 'wss://gw.example.org', 'expiresin' => 30, 'readonly' => false],
            $ticket
        );
        [$find] = $this->manager->requests_to('GET', '/api/v1/sessions');
        $this->assertSame("user_id={$this->student->id}&instance_id={$this->lab->id}", $find['query']);
        [$request] = $this->manager->requests_to('POST', self::TICKETS);
        $this->assertSame(['node_id' => fake_manager::NODE_TELNET, 'readonly' => false,
            'issued_for_user_id' => (int) $this->student->id], $request['body']);
    }

    public function test_student_cannot_get_a_ticket_to_another_lab(): void {
        $this->setUser($this->student);

        $this->expectException(\required_capability_exception::class);
        $this->ticket(fake_manager::NODE_TELNET, (int) $this->other->id);
    }

    public function test_teacher_gets_a_read_only_ticket_to_a_student_in_own_group(): void {
        $group = $this->getDataGenerator()->create_group(['courseid' => $this->course->id]);
        $this->getDataGenerator()->create_group_member(['groupid' => $group->id, 'userid' => $this->student->id]);
        $this->getDataGenerator()->create_group_member(['groupid' => $group->id, 'userid' => $this->teacher->id]);
        $this->prohibit_access_all_groups();
        $this->setUser($this->teacher);

        $ticket = $this->ticket(fake_manager::NODE_TELNET, (int) $this->student->id);

        $this->assertTrue($ticket['readonly']);
        [$request] = $this->manager->requests_to('POST', self::TICKETS);
        $this->assertTrue($request['body']['readonly']);
        $this->assertSame((int) $this->teacher->id, $request['body']['issued_for_user_id']);
    }

    public function test_teacher_outside_the_student_group_gets_nothing(): void {
        $group = $this->getDataGenerator()->create_group(['courseid' => $this->course->id]);
        $this->getDataGenerator()->create_group_member(['groupid' => $group->id, 'userid' => $this->student->id]);
        $this->prohibit_access_all_groups();
        $this->setUser($this->teacher);

        try {
            $this->ticket(fake_manager::NODE_TELNET, (int) $this->student->id);
            $this->fail('No exception');
        } catch (\moodle_exception $e) {
            $this->assertSame('cannotobserveuser', $e->errorcode);
        }
        $this->assertSame([], $this->manager->requests);
    }

    public function test_unknown_node_is_refused_before_asking_for_a_ticket(): void {
        $this->setUser($this->student);

        try {
            $this->ticket('6b1f3c2e-4a7d-4e5b-9c10-0000000000ff');
            $this->fail('No exception');
        } catch (\moodle_exception $e) {
            $this->assertSame('nodenotinsession', $e->errorcode);
        }
        $this->assertSame([], $this->manager->requests_to('POST', self::TICKETS));
    }

    public function test_vnc_node_is_refused(): void {
        $this->setUser($this->student);

        $this->expectExceptionMessage(get_string('consolenotsupported', 'gns3lab'));
        $this->ticket(fake_manager::NODE_VNC);
    }

    public function test_no_ticket_unless_running(): void {
        $this->manager->on('GET', '/api/v1/sessions', 200, fake_manager::session('starting'));
        $this->setUser($this->student);

        $this->expectExceptionMessage(get_string('sessionnotrunning', 'gns3lab'));
        $this->ticket();
    }

    public function test_gateway_url_falls_back_to_the_site_setting(): void {
        $this->manager->on('POST', self::TICKETS, 200, ['ticket' => 't-123', 'expires_in' => 30]);
        set_config('gatewayurl', 'wss://gw2.example.org', 'gns3lab');
        $this->setUser($this->student);

        $this->assertSame('wss://gw2.example.org', $this->ticket()['gatewayurl']);

        set_config('gatewayurl', 'https://not-a-websocket.example.org', 'gns3lab');
        $this->expectExceptionMessage(get_string('gatewaynotconfigured', 'gns3lab'));
        $this->ticket();
    }

    /**
     * Takes moodle/site:accessallgroups from non-editing teachers in the activity.
     */
    protected function prohibit_access_all_groups(): void {
        global $DB;
        $roleid = $DB->get_field('role', 'id', ['shortname' => 'teacher'], MUST_EXIST);
        assign_capability('moodle/site:accessallgroups', CAP_PROHIBIT, $roleid, \context_module::instance($this->lab->cmid));
    }

    public function test_stopped_node_is_reported_by_code(): void {
        $this->manager->on('POST', self::TICKETS, 409, ['error' => 'node_not_running', 'message' => 'Node is not running']);
        $this->setUser($this->student);

        try {
            $this->ticket();
            $this->fail('No exception');
        } catch (\moodle_exception $e) {
            // The console waits for the node on this code instead of reconnecting.
            $this->assertSame('managererror_node_not_running', $e->errorcode);
        }
    }
}
