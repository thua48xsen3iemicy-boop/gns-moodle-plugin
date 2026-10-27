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
use mod_gns3lab\event\seat_denied;
use mod_gns3lab\local\seat;
use mod_gns3lab\tests\fake_manager;

/**
 * Tests of the one-seat rule in the external functions (docs/seat.md): who may start and stop the lab, open a console and
 * write the mask of a student, and what the log gets.
 *
 * @package    mod_gns3lab
 * @category   test
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
#[\PHPUnit\Framework\Attributes\CoversClass(\mod_gns3lab\external\claim_seat::class)]
#[\PHPUnit\Framework\Attributes\CoversClass(\mod_gns3lab\local\seat::class)]
final class seat_test extends \advanced_testcase {
    /** Ticket route of the running session. */
    private const TICKETS = '/api/v1/sessions/s-3f9a1c0d2b4e5f60/console-tickets';

    /** @var fake_manager */
    protected fake_manager $manager;

    /** @var \stdClass course */
    protected \stdClass $course;

    /** @var \stdClass activity with cmid */
    protected \stdClass $lab;

    /** @var \stdClass student */
    protected \stdClass $student;

    /** @var \stdClass teacher */
    protected \stdClass $teacher;

    protected function setUp(): void {
        parent::setUp();
        $this->resetAfterTest();
        $gen = $this->getDataGenerator();
        $this->course = $gen->create_course();
        $this->lab = $gen->create_module('gns3lab', ['course' => $this->course->id]);
        $this->student = $gen->create_and_enrol($this->course, 'student');
        $this->teacher = $gen->create_and_enrol($this->course, 'teacher');

        $this->manager = new fake_manager();
        $this->manager->install();
        $this->manager->on('GET', '/api/v1/sessions', 200, fake_manager::session());
        $this->manager->on('POST', '/api/v1/sessions', 201, fake_manager::session());
        $this->manager->on('POST', '/api/v1/sessions/s-3f9a1c0d2b4e5f60/stop', 200, fake_manager::session('stopping'));
        $this->manager->on(
            'POST',
            self::TICKETS,
            200,
            ['ticket' => 't-123', 'gateway_url' => 'wss://gw.example.org/', 'expires_in' => 30]
        );
    }

    /**
     * Makes another login hold the seat of the student: a row of the table with a login that is not this request's.
     *
     * @param \stdClass|null $lab activity, the default one if null
     * @param string $tabid page of that login
     */
    protected function another_login_holds_the_seat(?\stdClass $lab = null, string $tabid = 'other'): void {
        global $DB;
        $DB->insert_record('gns3lab_seat', (object) [
            'gns3labid' => ($lab ?? $this->lab)->id,
            'userid' => $this->student->id,
            'sessionkey' => hash('sha256', 'the session of somebody else'),
            'tabid' => $tabid,
            'timeclaimed' => time(),
            'timemodified' => time(),
        ]);
    }

    /**
     * Calls claim_seat and cleans the result like the web service layer does.
     *
     * @param string $tabid page id
     * @param bool $takeover the page was just opened
     * @return array
     */
    protected function claim(string $tabid, bool $takeover = false): array {
        $result = claim_seat::execute($this->lab->cmid, $tabid, $takeover);
        return external_api::clean_returnvalue(claim_seat::execute_returns(), $result);
    }

    /**
     * Runs a call that needs the seat and returns the error code it was refused with.
     *
     * @param callable $call the call
     * @return string error code, empty if the call went through
     */
    protected function refusal(callable $call): string {
        try {
            $call();
        } catch (\moodle_exception $e) {
            return $e->errorcode;
        }
        return '';
    }

    public function test_a_page_claims_the_seat(): void {
        $this->setUser($this->student);

        $result = $this->claim('page1', true);

        $this->assertSame(['status' => 'held', 'mode' => 'tab', 'idle' => 0, 'beat' => seat::BEAT], $result);
    }

    public function test_another_login_is_refused_and_the_log_gets_the_trace(): void {
        $this->another_login_holds_the_seat();
        $this->setUser($this->student);
        $sink = $this->redirectEvents();

        $result = $this->claim('page1', true);

        $this->assertSame('otherlogin', $result['status']);
        $events = array_values(array_filter($sink->get_events(), fn($e) => $e instanceof seat_denied));
        $this->assertCount(1, $events);
        $this->assertEquals($this->student->id, $events[0]->relateduserid);
        $this->assertSame(['mode' => 'tab', 'where' => 'page'], $events[0]->other);
        $this->assertEquals($this->lab->cmid, $events[0]->contextinstanceid);
    }

    public function test_a_beat_of_a_refused_page_is_not_logged(): void {
        $this->another_login_holds_the_seat();
        $this->setUser($this->student);
        $sink = $this->redirectEvents();

        $this->assertSame('otherlogin', $this->claim('page1', false)['status']);
        $this->assertSame('otherlogin', $this->claim('page1', false)['status']);

        $this->assertSame([], array_filter($sink->get_events(), fn($e) => $e instanceof seat_denied));
    }

    public function test_another_page_of_the_same_login_is_not_logged(): void {
        $this->setUser($this->student);
        $this->claim('old', true);
        $sink = $this->redirectEvents();

        $this->assertSame('othertab', $this->claim('beat-of-someone-else', false)['status']);
        $this->assertSame('held', $this->claim('new', true)['status']);

        $this->assertSame([], array_filter($sink->get_events(), fn($e) => $e instanceof seat_denied));
    }

    public function test_the_page_id_is_checked(): void {
        $this->setUser($this->student);

        $this->expectException(\invalid_parameter_exception::class);
        $this->claim(str_repeat('a', seat::TABID_MAX + 1));
    }

    public function test_start_stop_ticket_and_mask_need_the_seat(): void {
        $this->another_login_holds_the_seat();
        $this->setUser($this->student);
        $sink = $this->redirectEvents();

        $this->assertSame('seatotherlogin', $this->refusal(fn() => start_session::execute($this->lab->cmid, 'page1')));
        $this->assertSame('seatotherlogin', $this->refusal(fn() => stop_session::execute($this->lab->cmid, 'page1')));
        $this->assertSame('seatotherlogin', $this->refusal(
            fn() => get_console_ticket::execute($this->lab->cmid, fake_manager::NODE_TELNET, 0, 'page1')
        ));
        $this->assertSame('seatotherlogin', $this->refusal(fn() => reveal::execute($this->lab->cmid, [], 'page1')));

        $this->assertSame([], $this->manager->requests_to('POST', '/api/v1/sessions'), 'the manager was not asked to start');
        $this->assertSame([], $this->manager->requests_to('POST', self::TICKETS), 'and gave no ticket');
        $this->assertCount(4, array_filter($sink->get_events(), fn($e) => $e instanceof seat_denied));
    }

    public function test_the_holder_works_as_before(): void {
        $this->setUser($this->student);
        $this->claim('page1', true);

        $session = start_session::execute($this->lab->cmid, 'page1');
        $ticket = get_console_ticket::execute($this->lab->cmid, fake_manager::NODE_TELNET, 0, 'page1');

        $this->assertSame('running', $session['status']);
        $this->assertSame('t-123', $ticket['ticket']);
    }

    public function test_the_page_that_lost_the_seat_to_another_tab_is_refused_with_its_own_code(): void {
        $this->setUser($this->student);
        $this->claim('old', true);
        $this->claim('new', true);

        $this->assertSame('seatothertab', $this->refusal(fn() => start_session::execute($this->lab->cmid, 'old')));
        $this->assertSame('', $this->refusal(fn() => start_session::execute($this->lab->cmid, 'new')));
    }

    public function test_a_call_claims_the_seat_when_it_is_free(): void {
        $this->setUser($this->student);

        // No beat yet: the first call that needs the seat takes it.
        $this->assertSame('', $this->refusal(fn() => start_session::execute($this->lab->cmid, 'page1')));
        $this->assertSame('held', $this->claim('page1')['status']);
    }

    public function test_a_teacher_looks_at_a_lab_whose_seat_is_held_by_somebody_else(): void {
        $this->another_login_holds_the_seat();
        $this->setUser($this->teacher);

        $ticket = get_console_ticket::execute($this->lab->cmid, fake_manager::NODE_TELNET, (int) $this->student->id);

        $this->assertTrue($ticket['readonly']);
    }

    public function test_in_an_activity_without_a_limit_the_gates_are_open(): void {
        $lab = $this->getDataGenerator()->create_module('gns3lab', ['course' => $this->course->id, 'seatmode' => 'off']);
        $this->another_login_holds_the_seat($lab);
        $this->setUser($this->student);

        $this->assertSame('', $this->refusal(fn() => start_session::execute($lab->cmid, 'page1')));
        $this->assertSame('off', claim_seat::execute($lab->cmid, 'page1')['mode']);
    }

    public function test_the_mode_is_saved_with_the_activity(): void {
        global $DB;

        $this->assertSame('tab', $DB->get_field('gns3lab', 'seatmode', ['id' => $this->lab->id]), 'the default');
        $lab = $this->getDataGenerator()->create_module('gns3lab', ['course' => $this->course->id, 'seatmode' => 'login']);
        $this->assertSame('login', $DB->get_field('gns3lab', 'seatmode', ['id' => $lab->id]));
        $odd = $this->getDataGenerator()->create_module('gns3lab', ['course' => $this->course->id, 'seatmode' => 'nonsense']);
        $this->assertSame(
            'tab',
            $DB->get_field('gns3lab', 'seatmode', ['id' => $odd->id]),
            'a value nobody knows becomes the default'
        );
    }
}
