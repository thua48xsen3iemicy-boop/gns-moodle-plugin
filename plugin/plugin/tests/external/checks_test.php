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
use mod_gns3lab\local\check_runs;
use mod_gns3lab\local\checks;
use mod_gns3lab\tests\fake_manager;

/**
 * Tests of the auto-check external functions (docs/autochecks.md): who starts and reads which checks, the grading
 * sent to the manager, attempts, what a student sees of the report and the grade in the gradebook.
 *
 * @package    mod_gns3lab
 * @category   test
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
#[\PHPUnit\Framework\Attributes\CoversClass(\mod_gns3lab\external\start_check::class)]
#[\PHPUnit\Framework\Attributes\CoversClass(\mod_gns3lab\external\get_checks::class)]
#[\PHPUnit\Framework\Attributes\CoversClass(\mod_gns3lab\external\get_check_run::class)]
#[\PHPUnit\Framework\Attributes\CoversClass(\mod_gns3lab\local\check_runs::class)]
#[\PHPUnit\Framework\Attributes\CoversClass(\mod_gns3lab\local\checks::class)]
final class checks_test extends \advanced_testcase {
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
        $this->lab = $gen->create_module('gns3lab', [
            'course' => $this->course->id,
            'grade' => 10,
            'checksenabled' => 1,
            'checksprofile' => 'strict',
            'checksoverridestext' => "hostname_wrong=note\nvlan_extra=minor",
            'checksattempts' => 2,
            'checksfailmark' => 2,
            'checksreport' => checks::REPORT_FULL,
        ]);
        $this->student = $gen->create_and_enrol($this->course, 'student');
        $this->teacher = $gen->create_and_enrol($this->course, 'teacher');
        $this->manager = new fake_manager();
        $this->manager->install();
        $this->manager->with_checks();
    }

    /**
     * Calls start_check and cleans the result.
     *
     * @return array
     */
    protected function start(): array {
        return external_api::clean_returnvalue(start_check::execute_returns(), start_check::execute($this->lab->cmid, 'tab1'));
    }

    /**
     * Calls get_checks and cleans the result.
     *
     * @param int $userid whose lab
     * @return array
     */
    protected function overview(int $userid = 0): array {
        return external_api::clean_returnvalue(get_checks::execute_returns(), get_checks::execute($this->lab->cmid, $userid));
    }

    /**
     * Calls get_check_run and cleans the result.
     *
     * @param int $userid whose lab
     * @return array
     */
    protected function read_run(int $userid = 0): array {
        return external_api::clean_returnvalue(
            get_check_run::execute_returns(),
            get_check_run::execute($this->lab->cmid, fake_manager::RUN, $userid)
        );
    }

    /**
     * Makes the manager answer the run as done with a mark.
     *
     * @param int $mark mark
     * @param string $outcome graded or incomplete
     */
    protected function finish(int $mark = 4, string $outcome = 'graded'): void {
        $this->manager->on(
            'GET',
            fake_manager::CHECKS . '/' . fake_manager::RUN,
            200,
            fake_manager::check_run('done', $outcome, $mark)
        );
    }

    public function test_student_starts_a_check_with_the_grading_of_the_activity(): void {
        $this->setUser($this->student);

        $result = $this->start();

        [$post] = $this->manager->requests_to('POST', fake_manager::CHECKS);
        $this->assertSame((int) $this->student->id, $post['body']['requested_by_user_id']);
        $this->assertSame('strict', $post['body']['grading']['profile']);
        $this->assertSame(['hostname_wrong' => 'note', 'vlan_extra' => 'minor'], $post['body']['grading']['overrides']);
        $this->assertSame(checks::DEFAULT_THRESHOLDS, $post['body']['grading']['thresholds']);
        $this->assertSame(2, $post['body']['grading']['fail_mark']);
        $this->assertTrue($result['canstart']);
        $this->assertSame(['max' => 2, 'used' => 0, 'left' => 2], $result['attempts']);
        $this->assertCount(1, $result['runs']);
        $this->assertSame(fake_manager::RUN, $result['runs'][0]['runid']);
        $this->assertSame('queued', $result['runs'][0]['status']);
        // The session is found by the server for this user, never named by the browser.
        [$find] = $this->manager->requests_to('GET', '/api/v1/sessions');
        $this->assertSame("user_id={$this->student->id}&instance_id={$this->lab->id}", $find['query']);
    }

    public function test_a_run_in_progress_is_followed_instead_of_a_new_one(): void {
        $this->setUser($this->student);
        $this->start();
        $this->manager->on('GET', fake_manager::CHECKS . '/' . fake_manager::RUN, 200, fake_manager::check_run('running'));

        $result = $this->start();

        $this->assertCount(1, $this->manager->requests_to('POST', fake_manager::CHECKS));
        $this->assertCount(1, $result['runs']);
        $this->assertSame('running', $result['runs'][0]['status']);
    }

    public function test_check_in_progress_of_the_manager_is_recorded(): void {
        $this->manager->on(
            'POST',
            fake_manager::CHECKS,
            409,
            ['error' => 'check_in_progress', 'message' => 'busy', 'run_id' => fake_manager::RUN]
        );
        $this->manager->on('GET', fake_manager::CHECKS . '/' . fake_manager::RUN, 200, fake_manager::check_run('running'));
        $this->setUser($this->student);

        $result = $this->start();

        $this->assertSame(fake_manager::RUN, $result['runs'][0]['runid']);
        $this->assertSame('running', $result['runs'][0]['status']);
    }

    public function test_graded_run_writes_the_grade_and_spends_an_attempt(): void {
        global $CFG;
        require_once($CFG->libdir . '/gradelib.php');
        $this->setUser($this->student);
        $this->start();
        $this->finish(4);

        $run = $this->read_run();

        $this->assertSame('done', $run['status']);
        $this->assertSame('graded', $run['outcome']);
        $this->assertSame(4, $run['mark']);
        $this->assertSame(['critical' => 0, 'major' => 1, 'minor' => 2, 'note' => 1], $run['counts']);
        $this->assertTrue($run['detailed']);
        $this->assertCount(2, $run['findings']);
        $this->assertSame('f3', $run['findings'][1]['causedby']);
        $this->assertSame([['node' => 'PC1', 'iface' => 'ens4']], $run['findings'][0]['ifaces']);
        $this->assertSame([], $run['rules']); // What was not checked is told to the teacher only.
        $this->assertSame(['max' => 2, 'used' => 1, 'left' => 1], $this->overview()['attempts']);
        // Mark 4 of the top mark 5, grade out of 10.
        $grades = grade_get_grades($this->course->id, 'mod', 'gns3lab', $this->lab->id, $this->student->id);
        $this->assertEquals(8.0, (float) $grades->items[0]->grades[$this->student->id]->grade);
    }

    public function test_incomplete_and_failed_runs_are_not_attempts(): void {
        global $CFG;
        require_once($CFG->libdir . '/gradelib.php');
        $this->setUser($this->student);
        $this->start();
        $this->finish(0, 'incomplete');

        $run = $this->read_run();

        $this->assertSame('incomplete', $run['outcome']);
        $this->assertSame(-1, $run['mark']);
        $this->assertSame(0, $this->overview()['attempts']['used']);
        $grades = grade_get_grades($this->course->id, 'mod', 'gns3lab', $this->lab->id, $this->student->id);
        $this->assertNull($grades->items[0]->grades[$this->student->id]->grade);

        $this->manager->on('GET', fake_manager::CHECKS . '/' . fake_manager::RUN, 200, fake_manager::check_run('failed'));
        check_runs::delete((int) $this->lab->id);
        $this->start();
        $run = $this->read_run();
        $this->assertSame('failed', $run['status']);
        $this->assertSame('SLOT_UNAVAILABLE', $run['errorcode']);
        $this->assertSame(0, $this->overview()['attempts']['used']);
    }

    public function test_no_attempts_left_refuses_before_the_manager_is_asked(): void {
        global $DB;
        $DB->set_field('gns3lab', 'checksattempts', 1, ['id' => $this->lab->id]);
        $this->setUser($this->student);
        $this->start();
        $this->finish(5);
        $this->read_run();

        try {
            $this->start();
            $this->fail('No exception');
        } catch (\moodle_exception $e) {
            $this->assertSame('checksnoattempts', $e->errorcode);
        }
        $this->assertCount(1, $this->manager->requests_to('POST', fake_manager::CHECKS));
        $this->assertFalse($this->overview()['canstart']);
    }

    public function test_the_last_graded_run_counts(): void {
        global $CFG, $DB;
        require_once($CFG->libdir . '/gradelib.php');
        $this->setUser($this->student);
        $this->start();
        $this->finish(5);
        $this->read_run();
        // A second run of the same session, graded lower.
        $second = fake_manager::check_run('queued');
        $second['run_id'] = 'c-0000000000000002';
        $this->manager->on('POST', fake_manager::CHECKS, 202, $second);
        $this->start();
        $done = fake_manager::check_run('done', 'graded', 3);
        $done['run_id'] = $second['run_id'];
        $this->manager->on('GET', fake_manager::CHECKS . '/' . $second['run_id'], 200, $done);
        get_check_run::execute($this->lab->cmid, $second['run_id']);

        $this->assertSame(2, $DB->count_records('gns3lab_check_runs', ['gns3labid' => $this->lab->id]));
        $grades = grade_get_grades($this->course->id, 'mod', 'gns3lab', $this->lab->id, $this->student->id);
        $this->assertEquals(6.0, (float) $grades->items[0]->grades[$this->student->id]->grade);
    }

    public function test_counts_only_strips_the_findings_for_the_student_not_the_teacher(): void {
        global $DB;
        $DB->set_field('gns3lab', 'checksreport', checks::REPORT_COUNTS, ['id' => $this->lab->id]);
        // The non-editing teacher sees the student in separate groups only through a shared group.
        $group = $this->getDataGenerator()->create_group(['courseid' => $this->course->id]);
        $this->getDataGenerator()->create_group_member(['groupid' => $group->id, 'userid' => $this->student->id]);
        $this->getDataGenerator()->create_group_member(['groupid' => $group->id, 'userid' => $this->teacher->id]);
        $this->setUser($this->student);
        $this->start();
        $this->finish(4);

        $run = $this->read_run();

        $this->assertFalse($run['detailed']);
        $this->assertSame([], $run['findings']);
        $this->assertSame([], $run['rules']);
        $this->assertSame(-1, $run['detailsfrom']);
        $this->assertSame(4, $run['mark']);

        $this->setUser($this->teacher);
        $run = $this->read_run((int) $this->student->id);
        $this->assertTrue($run['detailed']);
        $this->assertCount(2, $run['findings']);
    }

    public function test_a_teacher_sees_only_the_gaps_in_the_data(): void {
        // The non-editing teacher sees the student in separate groups only through a shared group.
        $group = $this->getDataGenerator()->create_group(['courseid' => $this->course->id]);
        $this->getDataGenerator()->create_group_member(['groupid' => $group->id, 'userid' => $this->student->id]);
        $this->getDataGenerator()->create_group_member(['groupid' => $group->id, 'userid' => $this->teacher->id]);
        $this->setUser($this->student);
        $this->start();
        $this->finish(4);

        $this->setUser($this->teacher);
        $run = $this->read_run((int) $this->student->id);

        // Not no_expectation (the lab does not ask for it), not off, not pass.
        $this->assertSame(
            [['code' => 'address_not_saved', 'group' => 'addressing', 'status' => 'skipped', 'reason' => 'no_collector']],
            $run['rules']
        );
    }

    public function test_findings_after_a_date(): void {
        global $DB;
        $DB->set_field('gns3lab', 'checksreport', checks::REPORT_AFTER, ['id' => $this->lab->id]);
        $DB->set_field('gns3lab', 'checksreportdate', time() + DAYSECS, ['id' => $this->lab->id]);
        $this->setUser($this->student);
        $this->start();
        $this->finish(4);

        $this->assertFalse($this->read_run()['detailed']);

        $DB->set_field('gns3lab', 'checksreportdate', time() - 1, ['id' => $this->lab->id]);
        $this->assertTrue($this->read_run()['detailed']);
    }

    public function test_disabled_checks_refuse_without_the_manager(): void {
        global $DB;
        $DB->set_field('gns3lab', 'checksenabled', 0, ['id' => $this->lab->id]);
        $this->setUser($this->student);

        try {
            $this->start();
            $this->fail('No exception');
        } catch (\moodle_exception $e) {
            $this->assertSame('checksdisabled', $e->errorcode);
        }
        $this->assertSame([], $this->manager->requests_to('POST', fake_manager::CHECKS));
        $this->assertFalse($this->overview()['enabled']);
    }

    public function test_session_without_the_feature_or_not_running(): void {
        $session = fake_manager::session();
        $this->manager->on('GET', '/api/v1/sessions', 200, $session);
        $this->setUser($this->student);
        try {
            $this->start();
            $this->fail('No exception');
        } catch (\moodle_exception $e) {
            $this->assertSame('checksnotavailable', $e->errorcode);
        }

        $this->manager->on('GET', '/api/v1/sessions', 200, fake_manager::session('stopped'));
        try {
            $this->start();
            $this->fail('No exception');
        } catch (\moodle_exception $e) {
            $this->assertSame('sessionnotrunning', $e->errorcode);
        }
        $this->assertSame([], $this->manager->requests_to('POST', fake_manager::CHECKS));
    }

    public function test_another_login_holding_the_seat_cannot_start(): void {
        global $DB;
        $DB->insert_record('gns3lab_seat', (object) [
            'gns3labid' => $this->lab->id, 'userid' => $this->student->id, 'sessionkey' => str_repeat('a', 64),
            'tabid' => 'other', 'timeclaimed' => time(), 'timemodified' => time(),
        ]);
        $this->setUser($this->student);

        try {
            $this->start();
            $this->fail('No exception');
        } catch (\moodle_exception $e) {
            $this->assertSame('seatotherlogin', $e->errorcode);
        }
        $this->assertSame([], $this->manager->requests_to('POST', fake_manager::CHECKS));
    }

    public function test_student_cannot_read_another_student(): void {
        $other = $this->getDataGenerator()->create_and_enrol($this->course, 'student');
        $this->setUser($this->student);

        $this->expectException(\required_capability_exception::class);
        $this->overview((int) $other->id);
    }

    public function test_a_run_of_another_student_is_not_found(): void {
        $other = $this->getDataGenerator()->create_and_enrol($this->course, 'student');
        $this->setUser($other);
        $this->start();
        $this->setUser($this->student);

        $this->expectException(\moodle_exception::class);
        $this->expectExceptionMessage(get_string('checknotfound', 'gns3lab'));
        $this->read_run();
    }

    public function test_teacher_outside_the_group_gets_nothing(): void {
        global $DB;
        $group = $this->getDataGenerator()->create_group(['courseid' => $this->course->id]);
        $this->getDataGenerator()->create_group_member(['groupid' => $group->id, 'userid' => $this->student->id]);
        $roleid = $DB->get_field('role', 'id', ['shortname' => 'teacher'], MUST_EXIST);
        assign_capability('moodle/site:accessallgroups', CAP_PROHIBIT, $roleid, \context_module::instance($this->lab->cmid));
        $this->setUser($this->teacher);

        try {
            $this->overview((int) $this->student->id);
            $this->fail('No exception');
        } catch (\moodle_exception $e) {
            $this->assertSame('cannotobserveuser', $e->errorcode);
        }
    }

    public function test_teacher_of_the_group_reads_but_cannot_start_for_the_student(): void {
        $group = $this->getDataGenerator()->create_group(['courseid' => $this->course->id]);
        $this->getDataGenerator()->create_group_member(['groupid' => $group->id, 'userid' => $this->student->id]);
        $this->getDataGenerator()->create_group_member(['groupid' => $group->id, 'userid' => $this->teacher->id]);
        $this->setUser($this->student);
        $this->start();
        $this->setUser($this->teacher);

        $result = $this->overview((int) $this->student->id);

        $this->assertFalse($result['canstart']);
        $this->assertCount(1, $result['runs']);
    }

    public function test_a_lost_run_is_failed_and_a_stale_one_given_up(): void {
        global $DB;
        $this->setUser($this->student);
        $this->start();
        $this->manager->on(
            'GET',
            fake_manager::CHECKS . '/' . fake_manager::RUN,
            404,
            ['error' => 'session_not_found', 'message' => 'gone']
        );

        $run = $this->read_run();

        $this->assertSame('failed', $run['status']);
        $this->assertSame(check_runs::LOST, $run['errorcode']);
        $this->assertTrue($run['gone']);

        $DB->set_field('gns3lab_check_runs', 'status', 'running', []);
        $DB->set_field('gns3lab_check_runs', 'timecreated', time() - 2 * DAYSECS, []);
        $this->assertSame(1, check_runs::give_up_stale(time()));
        $this->assertSame('failed', $DB->get_field('gns3lab_check_runs', 'status', []));
    }
}
