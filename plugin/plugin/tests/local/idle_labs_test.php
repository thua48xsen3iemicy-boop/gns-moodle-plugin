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
 * Tests of the idle stop: a lab left by its student is stopped (docs/idle-stop.md).
 *
 * @package    mod_gns3lab
 * @category   test
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
#[\PHPUnit\Framework\Attributes\CoversClass(\mod_gns3lab\local\idle_labs::class)]
final class idle_labs_test extends \advanced_testcase {
    /** The moment the tests happen at. */
    private const NOW = 1790000000;

    /** Stop route of the session of the fake manager. */
    private const STOP = '/api/v1/sessions/s-3f9a1c0d2b4e5f60/stop';

    /** @var \stdClass activity in mode tab */
    protected \stdClass $lab;

    /** @var \stdClass student */
    protected \stdClass $student;

    /** @var fake_manager lab manager */
    protected fake_manager $manager;

    /** @var manager_client client of the fake manager */
    protected manager_client $client;

    protected function setUp(): void {
        parent::setUp();
        $this->resetAfterTest();
        $gen = $this->getDataGenerator();
        $course = $gen->create_course();
        $this->lab = $gen->create_module('gns3lab', ['course' => $course->id]);
        $this->student = $gen->create_and_enrol($course, 'student');
        $this->manager = new fake_manager();
        $this->client = $this->manager->install();
        $this->manager->on('GET', '/api/v1/sessions', 200, fake_manager::session());
        $this->manager->on('POST', self::STOP, 200, fake_manager::session('stopping'));
    }

    /**
     * Puts the seat of the student into the table.
     *
     * @param int $age seconds since the last beat of the lab page
     * @return int id of the row
     */
    protected function seat(int $age): int {
        global $DB;
        return $DB->insert_record('gns3lab_seat', (object) [
            'gns3labid' => $this->lab->id,
            'userid' => $this->student->id,
            'sessionkey' => str_repeat('a', 64),
            'tabid' => 'x',
            'timeclaimed' => self::NOW - $age,
            'timemodified' => self::NOW - $age,
        ]);
    }

    /**
     * Number of stop requests the manager got.
     *
     * @return int
     */
    protected function stops(): int {
        return count($this->manager->requests_to('POST', self::STOP));
    }

    public function test_timeout_setting(): void {
        $this->assertSame(idle_labs::DEFAULT_TIMEOUT * MINSECS, idle_labs::timeout());
        set_config('idletimeout', 0, 'gns3lab');
        $this->assertSame(0, idle_labs::timeout());
        set_config('idletimeout', 1, 'gns3lab');
        $this->assertSame(idle_labs::MIN_TIMEOUT * MINSECS, idle_labs::timeout());
        set_config('idletimeout', 45, 'gns3lab');
        $this->assertSame(45 * MINSECS, idle_labs::timeout());
    }

    public function test_a_left_lab_is_stopped_once(): void {
        global $DB;

        $id = $this->seat(31 * MINSECS);
        $sink = $this->redirectEvents();

        $this->assertSame(1, idle_labs::stop_idle($this->client, self::NOW));
        $this->assertSame(1, $this->stops());
        $events = array_filter($sink->get_events(), fn($e) => $e instanceof \mod_gns3lab\event\lab_idle_stopped);
        $this->assertCount(1, $events);
        $event = reset($events);
        $this->assertEquals($this->student->id, $event->relateduserid);
        $this->assertSame(self::NOW - 31 * MINSECS, $event->other['lastseen']);
        $this->assertEquals(self::NOW - 31 * MINSECS, $DB->get_field('gns3lab_seat', 'timeidlechecked', ['id' => $id]));

        // The next run does not ask the manager about the same absence again.
        $asked = count($this->manager->requests);
        $this->assertSame(0, idle_labs::stop_idle($this->client, self::NOW + 5 * MINSECS));
        $this->assertCount($asked, $this->manager->requests);
    }

    public function test_a_lab_with_an_open_page_is_left_alone(): void {
        $this->seat(29 * MINSECS);
        $this->assertSame(0, idle_labs::stop_idle($this->client, self::NOW));
        $this->assertCount(0, $this->manager->requests);
    }

    public function test_switched_off(): void {
        set_config('idletimeout', 0, 'gns3lab');
        $this->seat(DAYSECS);
        $this->assertSame(0, idle_labs::stop_idle($this->client, self::NOW));
        $this->assertCount(0, $this->manager->requests);
    }

    public function test_an_activity_without_the_seat_is_left_alone(): void {
        global $DB;

        $DB->set_field('gns3lab', 'seatmode', seat::OFF, ['id' => $this->lab->id]);
        $this->seat(HOURSECS);
        $this->assertSame(0, idle_labs::stop_idle($this->client, self::NOW));
        $this->assertCount(0, $this->manager->requests);
    }

    /**
     * Sessions that need no stop.
     *
     * @return array each: status of the session, null for no session at all
     */
    public static function nothing_to_stop_provider(): array {
        return [
            'no session' => [null],
            'stopped' => ['stopped'],
            'failed' => ['failed'],
            'stopping' => ['stopping'],
        ];
    }

    #[\PHPUnit\Framework\Attributes\DataProvider('nothing_to_stop_provider')]
    public function test_nothing_to_stop(?string $status): void {
        global $DB;

        if ($status === null) {
            $this->manager->on('GET', '/api/v1/sessions', 404, ['error' => 'not_found', 'message' => 'No session']);
        } else {
            $this->manager->on('GET', '/api/v1/sessions', 200, fake_manager::session($status));
        }
        $id = $this->seat(HOURSECS);

        $this->assertSame(0, idle_labs::stop_idle($this->client, self::NOW));
        $this->assertSame(0, $this->stops());
        // Looked at: not asked again until the student comes back.
        $this->assertEquals(self::NOW - HOURSECS, $DB->get_field('gns3lab_seat', 'timeidlechecked', ['id' => $id]));
    }

    public function test_a_lab_started_after_coming_back_is_stopped_again(): void {
        global $DB;

        $id = $this->seat(HOURSECS);
        $this->assertSame(1, idle_labs::stop_idle($this->client, self::NOW));

        // The student comes back, the page beats, the student starts the lab and leaves again.
        $DB->set_field('gns3lab_seat', 'timemodified', self::NOW + 10 * MINSECS, ['id' => $id]);
        $this->assertSame(0, idle_labs::stop_idle($this->client, self::NOW + 20 * MINSECS));
        $this->assertSame(1, idle_labs::stop_idle($this->client, self::NOW + 41 * MINSECS));
        $this->assertSame(2, $this->stops());
    }

    public function test_a_failed_request_is_tried_again(): void {
        global $DB;

        $this->manager->on('POST', self::STOP, 503, ['error' => 'unavailable', 'message' => 'Try later']);
        $id = $this->seat(HOURSECS);

        ob_start();
        $this->assertSame(0, idle_labs::stop_idle($this->client, self::NOW));
        ob_end_clean();
        $this->assertEquals(0, $DB->get_field('gns3lab_seat', 'timeidlechecked', ['id' => $id]));

        $this->manager->on('POST', self::STOP, 200, fake_manager::session('stopping'));
        $this->assertSame(1, idle_labs::stop_idle($this->client, self::NOW + 5 * MINSECS));
    }
}
