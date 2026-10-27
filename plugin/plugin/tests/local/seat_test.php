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

/**
 * Tests of the seat: who may drive the lab of a student right now (docs/seat.md).
 *
 * @package    mod_gns3lab
 * @category   test
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
#[\PHPUnit\Framework\Attributes\CoversClass(\mod_gns3lab\local\seat::class)]
final class seat_test extends \advanced_testcase {
    /** The moment the tests happen at. */
    private const NOW = 1790000000;

    /** @var \stdClass activity in mode tab */
    protected \stdClass $lab;

    /** @var \stdClass student */
    protected \stdClass $student;

    protected function setUp(): void {
        parent::setUp();
        $this->resetAfterTest();
        $gen = $this->getDataGenerator();
        $course = $gen->create_course();
        $this->lab = $gen->create_module('gns3lab', ['course' => $course->id]);
        $this->student = $gen->create_and_enrol($course, 'student');
    }

    /**
     * Who gets the seat: the pure decision.
     *
     * @return array each: mode, the seat as stored (null for none), login, page, takeover, expected status
     */
    public static function verdict_provider(): array {
        $held = fn(string $login, string $tab, int $age) => (object) [
            'sessionkey' => $login, 'tabid' => $tab, 'timemodified' => self::NOW - $age,
        ];
        return [
            'no limit' => [seat::OFF, $held('A', 'x', 1), 'B', 'y', false, seat::HELD],
            'nobody has the seat' => [seat::TAB, null, 'A', 'x', false, seat::HELD],
            'the seat has expired' => [seat::TAB, $held('B', 'y', seat::TTL + 1), 'A', 'x', false, seat::HELD],
            'the last second of the lifetime' => [seat::TAB, $held('B', 'y', seat::TTL), 'A', 'x', false, seat::OTHER_LOGIN],
            'the holder itself' => [seat::TAB, $held('A', 'x', 5), 'A', 'x', false, seat::HELD],
            'another page of the same login, a beat' => [seat::TAB, $held('A', 'y', 5), 'A', 'x', false, seat::OTHER_TAB],
            'another page of the same login, just opened' => [seat::TAB, $held('A', 'y', 5), 'A', 'x', true, seat::HELD],
            'another login, a beat' => [seat::TAB, $held('B', 'y', 5), 'A', 'x', false, seat::OTHER_LOGIN],
            'another login, just opened: never taken over' => [
                seat::TAB, $held('B', 'y', 5), 'A', 'x', true, seat::OTHER_LOGIN,
            ],
            'login mode: another page of the same login' => [seat::LOGIN, $held('A', 'y', 5), 'A', 'x', false, seat::HELD],
            'login mode: another login' => [seat::LOGIN, $held('B', 'y', 5), 'A', 'x', true, seat::OTHER_LOGIN],
        ];
    }

    #[\PHPUnit\Framework\Attributes\DataProvider('verdict_provider')]
    public function test_verdict(
        string $mode,
        ?\stdClass $row,
        string $login,
        string $tab,
        bool $takeover,
        string $expected
    ): void {
        $this->assertSame($expected, seat::verdict($mode, $row, $login, $tab, $takeover, self::NOW));
    }

    public function test_an_unknown_mode_is_the_strictest(): void {
        $this->assertSame(seat::TAB, seat::mode((object) ['seatmode' => 'whatever']));
        $this->assertSame(seat::TAB, seat::mode((object) []));
        $this->assertSame(seat::LOGIN, seat::mode((object) ['seatmode' => 'login']));
        $this->assertSame(seat::TAB, seat::mode($this->lab), 'a new activity starts in the default mode');
    }

    public function test_the_session_key_is_a_hash_and_does_not_change_within_a_request(): void {
        $this->setUser($this->student);
        $key = seat::session_key();
        $this->assertMatchesRegularExpression('/^[0-9a-f]{64}$/', $key);
        $this->assertSame($key, seat::session_key());
    }

    public function test_the_first_page_gets_the_seat_and_the_second_login_does_not(): void {
        global $DB;
        $id = (int) $this->student->id;

        $first = seat::claim($this->lab, $id, 'page1', true, 'login-A', self::NOW);
        $this->assertSame(['status' => seat::HELD, 'idle' => 0], $first);
        $row = $DB->get_record('gns3lab_seat', ['gns3labid' => $this->lab->id, 'userid' => $id], '*', MUST_EXIST);
        $this->assertSame('login-A', $row->sessionkey);
        $this->assertSame('page1', $row->tabid);

        $second = seat::claim($this->lab, $id, 'page9', true, 'login-B', self::NOW + 30);
        $this->assertSame(['status' => seat::OTHER_LOGIN, 'idle' => 30], $second);
        $after = $DB->get_record('gns3lab_seat', ['id' => $row->id]);
        $this->assertEquals($row, $after, 'a refusal changes nothing');
        $this->assertSame(1, $DB->count_records('gns3lab_seat'));
    }

    public function test_a_beat_keeps_the_seat_but_writes_only_every_third_of_the_lifetime(): void {
        global $DB;
        $id = (int) $this->student->id;
        seat::claim($this->lab, $id, 'page1', true, 'login-A', self::NOW);

        seat::claim($this->lab, $id, 'page1', false, 'login-A', self::NOW + 10);
        $this->assertEquals(self::NOW, $DB->get_field('gns3lab_seat', 'timemodified', ['userid' => $id]), 'too early to write');

        seat::claim($this->lab, $id, 'page1', false, 'login-A', self::NOW + 31);
        $this->assertEquals(self::NOW + 31, $DB->get_field('gns3lab_seat', 'timemodified', ['userid' => $id]));
        $this->assertEquals(
            self::NOW,
            $DB->get_field('gns3lab_seat', 'timeclaimed', ['userid' => $id]),
            'the seat did not change hands'
        );
    }

    public function test_a_new_page_takes_the_seat_and_the_old_one_is_told_by_its_next_beat(): void {
        global $DB;
        $id = (int) $this->student->id;
        seat::claim($this->lab, $id, 'old', true, 'login-A', self::NOW);

        $new = seat::claim($this->lab, $id, 'new', true, 'login-A', self::NOW + 5);
        $this->assertSame(seat::HELD, $new['status']);
        $this->assertEquals(self::NOW + 5, $DB->get_field('gns3lab_seat', 'timeclaimed', ['userid' => $id]));

        $beat = seat::claim($this->lab, $id, 'old', false, 'login-A', self::NOW + 25);
        $this->assertSame(seat::OTHER_TAB, $beat['status'], 'a beat never takes the seat back: two pages would fight over it');
        $this->assertSame('new', $DB->get_field('gns3lab_seat', 'tabid', ['userid' => $id]));

        $back = seat::claim($this->lab, $id, 'old', true, 'login-A', self::NOW + 26);
        $this->assertSame(seat::HELD, $back['status'], 'the student chose "use here"');
    }

    public function test_the_seat_goes_to_another_login_when_the_holder_is_gone(): void {
        global $DB;
        $id = (int) $this->student->id;
        seat::claim($this->lab, $id, 'page1', true, 'login-A', self::NOW);

        $late = seat::claim($this->lab, $id, 'page9', true, 'login-B', self::NOW + seat::TTL + 1);

        $this->assertSame(seat::HELD, $late['status']);
        $this->assertSame('login-B', $DB->get_field('gns3lab_seat', 'sessionkey', ['userid' => $id]));
        $this->assertSame(1, $DB->count_records('gns3lab_seat'), 'one row per student and activity');
    }

    public function test_a_page_that_slept_takes_the_seat_back_if_nobody_took_it(): void {
        $id = (int) $this->student->id;
        seat::claim($this->lab, $id, 'page1', true, 'login-A', self::NOW);

        $woke = seat::claim($this->lab, $id, 'page1', false, 'login-A', self::NOW + 10 * seat::TTL);

        $this->assertSame(seat::HELD, $woke['status']);
    }

    public function test_login_mode_lets_the_pages_of_one_login_share_the_seat(): void {
        $lab = $this->getDataGenerator()->create_module('gns3lab', ['course' => $this->lab->course, 'seatmode' => 'login']);
        $id = (int) $this->student->id;
        seat::claim($lab, $id, 'page1', true, 'login-A', self::NOW);

        $this->assertSame(seat::HELD, seat::claim($lab, $id, 'page2', false, 'login-A', self::NOW + 1)['status']);
        $this->assertSame(seat::OTHER_LOGIN, seat::claim($lab, $id, 'page3', true, 'login-B', self::NOW + 2)['status']);
    }

    public function test_without_a_limit_nothing_is_stored(): void {
        global $DB;
        $lab = $this->getDataGenerator()->create_module('gns3lab', ['course' => $this->lab->course, 'seatmode' => 'off']);

        $result = seat::claim($lab, (int) $this->student->id, 'page1', false, 'login-A', self::NOW);

        $this->assertSame(['status' => seat::HELD, 'idle' => 0], $result);
        $this->assertSame(0, $DB->count_records('gns3lab_seat'));
    }

    public function test_seats_are_kept_per_student_and_activity(): void {
        $other = $this->getDataGenerator()->create_and_enrol(get_course($this->lab->course), 'student');
        $lab2 = $this->getDataGenerator()->create_module('gns3lab', ['course' => $this->lab->course]);

        seat::claim($this->lab, (int) $this->student->id, 'p', true, 'login-A', self::NOW);

        $this->assertSame(seat::HELD, seat::claim($this->lab, (int) $other->id, 'q', true, 'login-B', self::NOW)['status']);
        $this->assertSame(seat::HELD, seat::claim($lab2, (int) $this->student->id, 'q', true, 'login-B', self::NOW)['status']);
    }

    public function test_the_page_id_must_fit_the_column(): void {
        $this->assertSame('abc', seat::clean_tabid('abc'));
        $this->assertSame('', seat::clean_tabid(''));
        $this->expectException(\invalid_parameter_exception::class);
        seat::clean_tabid(str_repeat('a', seat::TABID_MAX + 1));
    }

    public function test_delete_by_activity_and_by_user(): void {
        global $DB;
        $other = $this->getDataGenerator()->create_and_enrol(get_course($this->lab->course), 'student');
        seat::claim($this->lab, (int) $this->student->id, 'p', true, 'login-A', self::NOW);
        seat::claim($this->lab, (int) $other->id, 'q', true, 'login-B', self::NOW);

        seat::delete((int) $this->lab->id, [(int) $this->student->id]);
        $this->assertSame([(int) $other->id], array_map('intval', $DB->get_fieldset_select('gns3lab_seat', 'userid', '1 = 1')));
        seat::delete((int) $this->lab->id, []);
        $this->assertSame(1, $DB->count_records('gns3lab_seat'));
        seat::delete((int) $this->lab->id);
        $this->assertSame(0, $DB->count_records('gns3lab_seat'));
    }

    public function test_deleting_the_activity_deletes_its_seats(): void {
        global $CFG, $DB;
        require_once($CFG->dirroot . '/course/lib.php');
        seat::claim($this->lab, (int) $this->student->id, 'p', true, 'login-A', self::NOW);

        course_delete_module($this->lab->cmid);

        $this->assertSame(0, $DB->count_records('gns3lab_seat'));
    }
}
