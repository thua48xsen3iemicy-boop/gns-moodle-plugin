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

namespace mod_gns3lab\external;

use core_external\external_api;
use mod_gns3lab\tests\fake_manager;

/**
 * Tests of mod_gns3lab_get_course_overview: who sees which students and labs on the course page of a teacher.
 *
 * @package    mod_gns3lab
 * @category   test
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
#[\PHPUnit\Framework\Attributes\CoversClass(\mod_gns3lab\external\get_course_overview::class)]
#[\PHPUnit\Framework\Attributes\CoversClass(\mod_gns3lab\local\course_labs::class)]
#[\PHPUnit\Framework\Attributes\CoversClass(\mod_gns3lab\local\activity_sessions::class)]
final class course_overview_test extends \advanced_testcase {
    /** @var fake_manager */
    protected fake_manager $manager;

    /** @var \stdClass course */
    protected \stdClass $course;

    /** @var \stdClass first lab */
    protected \stdClass $lab;

    /** @var \stdClass student */
    protected \stdClass $student;

    /** @var \stdClass second student */
    protected \stdClass $other;

    /** @var \stdClass non-editing teacher */
    protected \stdClass $teacher;

    protected function setUp(): void {
        parent::setUp();
        $this->resetAfterTest();
        $gen = $this->getDataGenerator();
        $this->course = $gen->create_course();
        $this->lab = $gen->create_module('gns3lab', ['course' => $this->course->id]);
        $this->student = $gen->create_and_enrol($this->course, 'student');
        $this->other = $gen->create_and_enrol($this->course, 'student');
        $this->teacher = $gen->create_and_enrol($this->course, 'teacher');
        $this->manager = new fake_manager();
        $this->manager->install();
        $this->sessions($this->lab, [
            fake_manager::session('running') + ['user_id' => (int) $this->student->id],
        ]);
    }

    /**
     * Sets the list of sessions the manager gives for an activity.
     *
     * The list of the manager has no topology, nodes or features (CONTRACTS §2.4).
     *
     * @param \stdClass $lab activity
     * @param array $sessions sessions with user_id
     */
    protected function sessions(\stdClass $lab, array $sessions): void {
        $this->manager->on('GET', '/api/v1/sessions', 200, array_map(function ($session) {
            unset($session['topology'], $session['nodes'], $session['features']);
            return $session;
        }, $sessions));
    }

    /**
     * Calls the function and cleans the result like the web service layer does.
     *
     * @param int $groupid group
     * @return array
     */
    protected function overview(int $groupid = 0): array {
        return external_api::clean_returnvalue(
            get_course_overview::execute_returns(),
            get_course_overview::execute((int) $this->course->id, $groupid)
        );
    }

    /**
     * Cell of a student in a lab.
     *
     * @param array $result result of the function
     * @param \stdClass $lab activity
     * @param \stdClass $user student
     * @return array|null
     */
    protected function cell(array $result, \stdClass $lab, \stdClass $user): ?array {
        foreach ($result['cells'] as $cell) {
            if ($cell['cmid'] == $lab->cmid && $cell['userid'] == $user->id) {
                return $cell;
            }
        }
        return null;
    }

    /**
     * Takes moodle/site:accessallgroups from non-editing teachers in the course and its activities.
     */
    protected function prohibit_access_all_groups(): void {
        global $DB;
        $roleid = $DB->get_field('role', 'id', ['shortname' => 'teacher'], MUST_EXIST);
        assign_capability('moodle/site:accessallgroups', CAP_PROHIBIT, $roleid, \context_course::instance($this->course->id));
    }

    /**
     * Puts an activity into the separate groups mode.
     *
     * @param \stdClass $lab activity
     */
    protected function separate_groups(\stdClass $lab): void {
        global $CFG;
        require_once($CFG->dirroot . '/course/lib.php');
        set_coursemodule_groupmode($lab->cmid, SEPARATEGROUPS);
        rebuild_course_cache($this->course->id, true);
    }

    public function test_a_student_is_refused(): void {
        $this->setUser($this->student);

        $this->expectException(\required_capability_exception::class);
        $this->overview();
    }

    public function test_a_teacher_gets_students_labs_and_sessions_without_ids(): void {
        $this->setUser($this->teacher);

        $result = $this->overview();

        $this->assertSame([(int) $this->lab->cmid], array_column($result['labs'], 'cmid'));
        $this->assertSame('', $result['labs'][0]['error']);
        $ids = array_column($result['students'], 'userid');
        sort($ids);
        $expected = [(int) $this->student->id, (int) $this->other->id];
        sort($expected);
        $this->assertSame($expected, $ids, 'the teacher is not a row');
        $running = $this->cell($result, $this->lab, $this->student);
        $this->assertTrue($running['observable']);
        $this->assertSame('running', $running['session']['status']);
        $this->assertGreaterThan(0, $running['session']['startedat']);
        $this->assertGreaterThan(0, $running['session']['expiresat']);
        $this->assertSame('unknown', $running['session']['facts'], 'the list of the manager has no features');
        $this->assertNull($this->cell($result, $this->lab, $this->other)['session']);
        $this->assertStringNotContainsString('s-3f9a1c0d2b4e5f60', json_encode($result), 'no session id');
        $this->assertStringNotContainsString('topology', json_encode($result));
        [$request] = $this->manager->requests_to('GET', '/api/v1/sessions');
        $this->assertSame("instance_id={$this->lab->id}", $request['query']);
    }

    public function test_the_sessions_of_a_lab_are_cached_for_a_few_seconds(): void {
        $this->setUser($this->teacher);

        $this->overview();
        $this->overview();

        $this->assertCount(1, $this->manager->requests_to('GET', '/api/v1/sessions'));
    }

    public function test_a_teacher_without_group_access_sees_only_the_students_of_own_groups(): void {
        $gen = $this->getDataGenerator();
        $mine = $gen->create_group(['courseid' => $this->course->id]);
        $theirs = $gen->create_group(['courseid' => $this->course->id]);
        $gen->create_group_member(['groupid' => $mine->id, 'userid' => $this->teacher->id]);
        $gen->create_group_member(['groupid' => $mine->id, 'userid' => $this->student->id]);
        $gen->create_group_member(['groupid' => $theirs->id, 'userid' => $this->other->id]);
        $this->prohibit_access_all_groups();
        $this->setUser($this->teacher);
        $this->separate_groups($this->lab);

        $result = $this->overview();

        $this->assertSame([(int) $this->student->id], array_column($result['students'], 'userid'));
        $this->assertNull($this->cell($result, $this->lab, $this->other), 'no cell, no name for a foreign group');

        // The group to pick from is one of the teacher's own.
        $this->assertSame([(int) $this->student->id], array_column($this->overview((int) $mine->id)['students'], 'userid'));
        $this->expectException(\invalid_parameter_exception::class);
        $this->overview((int) $theirs->id);
    }

    public function test_a_student_of_a_foreign_group_is_a_dash_in_a_lab_with_separate_groups(): void {
        $gen = $this->getDataGenerator();
        $second = $gen->create_module('gns3lab', ['course' => $this->course->id]);
        $mine = $gen->create_group(['courseid' => $this->course->id]);
        $theirs = $gen->create_group(['courseid' => $this->course->id]);
        $gen->create_group_member(['groupid' => $mine->id, 'userid' => $this->teacher->id]);
        $gen->create_group_member(['groupid' => $theirs->id, 'userid' => $this->other->id]);
        $this->prohibit_access_all_groups();
        $this->setUser($this->teacher);
        $this->separate_groups($this->lab);

        $result = $this->overview();

        $this->assertFalse($this->cell($result, $this->lab, $this->other)['observable']);
        $this->assertNull($this->cell($result, $this->lab, $this->other)['session']);
        $this->assertTrue($this->cell($result, $second, $this->other)['observable'], 'the second lab has no groups');
    }

    public function test_a_teacher_without_a_group_sees_nobody_in_a_lab_with_separate_groups(): void {
        $gen = $this->getDataGenerator();
        $theirs = $gen->create_group(['courseid' => $this->course->id]);
        $gen->create_group_member(['groupid' => $theirs->id, 'userid' => $this->student->id]);
        $this->prohibit_access_all_groups();
        $this->setUser($this->teacher);
        $this->separate_groups($this->lab);

        $result = $this->overview();

        $this->assertSame([], $result['students'], 'no group of the teacher, no student to observe');
        $this->assertSame([], $result['cells']);
    }

    public function test_a_lab_without_observe_is_not_in_the_answer(): void {
        global $DB;
        $second = $this->getDataGenerator()->create_module('gns3lab', ['course' => $this->course->id]);
        $roleid = $DB->get_field('role', 'id', ['shortname' => 'teacher'], MUST_EXIST);
        assign_capability('mod/gns3lab:observe', CAP_PROHIBIT, $roleid, \context_module::instance($second->cmid));
        $this->setUser($this->teacher);

        $result = $this->overview();

        $this->assertSame([(int) $this->lab->cmid], array_column($result['labs'], 'cmid'));
        $this->assertNull($this->cell($result, $second, $this->student));
        foreach ($this->manager->requests_to('GET', '/api/v1/sessions') as $request) {
            $this->assertSame("instance_id={$this->lab->id}", $request['query'], 'the manager is not asked about it');
        }
    }

    public function test_a_manager_that_does_not_answer_marks_only_its_lab(): void {
        $second = $this->getDataGenerator()->create_module('gns3lab', ['course' => $this->course->id]);
        $this->manager->on('GET', '/api/v1/sessions?instance_id=' . $second->id, 503, ['error' => 'unavailable', 'message' => 'x']);
        $this->setUser($this->teacher);

        $result = $this->overview();

        $this->assertSame([(int) $this->lab->cmid, (int) $second->cmid], array_column($result['labs'], 'cmid'));
        $this->assertSame('', $result['labs'][0]['error']);
        $this->assertNotSame('', $result['labs'][1]['error']);
        $this->assertSame('running', $this->cell($result, $this->lab, $this->student)['session']['status']);
        $cell = $this->cell($result, $second, $this->student);
        $this->assertTrue($cell['observable']);
        $this->assertNull($cell['session']);
    }

    public function test_without_the_manager_every_lab_has_an_error_and_the_page_still_works(): void {
        unset_config('managerurl', 'gns3lab');
        $this->setUser($this->teacher);

        $result = $this->overview();

        $this->assertSame('notconfigured', $result['labs'][0]['error']);
        $this->assertCount(2, $result['students']);
    }
}
