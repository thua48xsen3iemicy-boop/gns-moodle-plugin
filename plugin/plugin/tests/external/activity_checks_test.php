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
use mod_gns3lab\local\checks;
use mod_gns3lab\local\lab_library;
use mod_gns3lab\tests\fake_manager;

/**
 * Tests of the checks of the activity (docs/autochecks.md §12): the document goes with the start of a lab, an edit
 * reaches the live labs, a run by earlier checks is marked, checks that do not fit the lab refuse a check, a teacher's
 * trial run spends no attempt, and the form asks the lab manager about the document.
 *
 * @package    mod_gns3lab
 * @category   test
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
#[\PHPUnit\Framework\Attributes\CoversClass(\mod_gns3lab\local\session_checks::class)]
#[\PHPUnit\Framework\Attributes\CoversClass(\mod_gns3lab\local\checks_lab::class)]
#[\PHPUnit\Framework\Attributes\CoversClass(\mod_gns3lab\external\validate_checks::class)]
#[\PHPUnit\Framework\Attributes\CoversClass(\mod_gns3lab\external\get_checks_lab::class)]
#[\PHPUnit\Framework\Attributes\CoversClass(\mod_gns3lab\task\push_checks_definition::class)]
final class activity_checks_test extends \advanced_testcase {
    /** The checks of the activity in the tests. */
    private const DOC = '{"format":"gns3lab.checks/1","network":"off"}';

    /** @var fake_manager */
    protected fake_manager $manager;

    /** @var \stdClass course */
    protected \stdClass $course;

    /** @var \stdClass activity with cmid */
    protected \stdClass $lab;

    /** @var \stdClass student */
    protected \stdClass $student;

    /** @var \stdClass editing teacher */
    protected \stdClass $teacher;

    protected function setUp(): void {
        global $DB;
        parent::setUp();
        $this->resetAfterTest();
        $gen = $this->getDataGenerator();
        $this->course = $gen->create_course();
        $this->lab = $gen->create_module('gns3lab', [
            'course' => $this->course->id,
            'labref' => 'cisco-3x2',
            'grade' => 10,
            'checksenabled' => 1,
            'checksattempts' => 1,
        ]);
        $DB->update_record('gns3lab', (object) ['id' => $this->lab->id, 'checksdef' => self::DOC,
            'checksdefhash' => 'sha256:cccc']);
        $this->student = $gen->create_and_enrol($this->course, 'student');
        $this->teacher = $gen->create_and_enrol($this->course, 'editingteacher');
        $this->manager = new fake_manager();
        $this->manager->install();
        $this->manager->with_activity_checks();
    }

    /**
     * Calls start_check and cleans the result.
     *
     * @return array
     */
    protected function start_check(): array {
        return external_api::clean_returnvalue(start_check::execute_returns(), start_check::execute($this->lab->cmid, 'tab1'));
    }

    public function test_the_start_of_a_lab_carries_the_checks_of_the_activity(): void {
        $this->manager->on('POST', '/api/v1/sessions', 201, fake_manager::session('queued'));
        $this->setUser($this->student);

        start_session::execute($this->lab->cmid, 'tab1');

        [$post] = $this->manager->requests_to('POST', '/api/v1/sessions');
        $this->assertSame(['format' => 'gns3lab.checks/1', 'network' => 'off'], $post['body']['checks']);
        $this->assertSame('cisco-3x2', $post['body']['lab_ref']);
    }

    public function test_an_activity_without_own_checks_sends_none(): void {
        global $DB;
        $DB->set_field('gns3lab', 'checksdef', null, ['id' => $this->lab->id]);
        $this->manager->on('POST', '/api/v1/sessions', 201, fake_manager::session('queued'));
        $this->setUser($this->student);

        start_session::execute($this->lab->cmid, 'tab1');

        [$post] = $this->manager->requests_to('POST', '/api/v1/sessions');
        $this->assertArrayNotHasKey('checks', $post['body']);
    }

    public function test_a_lab_checked_by_other_checks_gets_those_of_the_activity_before_a_check(): void {
        $this->setUser($this->student);

        $this->start_check();

        [$put] = $this->manager->requests_to('PUT', fake_manager::CHECKS_DEFINITION);
        $this->assertSame(['checks' => ['format' => 'gns3lab.checks/1', 'network' => 'off']], $put['body']);
        $this->assertCount(1, $this->manager->requests_to('POST', fake_manager::CHECKS));
    }

    public function test_a_lab_with_the_checks_of_the_activity_is_not_asked_again(): void {
        $session = fake_manager::session();
        $session['features'] = ['consoles', 'facts', 'checks'];
        $session['checks_def'] = 'sha256:cccc';
        $session['checks_source'] = 'activity';
        $this->manager->on('GET', '/api/v1/sessions', 200, $session);
        $this->setUser($this->student);

        $this->start_check();

        $this->assertSame([], $this->manager->requests_to('PUT', fake_manager::CHECKS_DEFINITION));
        $this->assertCount(1, $this->manager->requests_to('POST', fake_manager::CHECKS));
    }

    public function test_a_lab_on_an_earlier_version_is_checked_by_its_own_and_the_run_is_marked(): void {
        $this->manager->on('PUT', fake_manager::CHECKS_DEFINITION, 422, ['error' => 'checks_invalid', 'message' => 'no',
            'problems' => [['path' => 'checks.scripts[0].node', 'code' => 'unknown_node', 'message' => 'PC1']]]);
        $run = fake_manager::check_run('queued');
        $run['checks_def'] = 'sha256:old';
        $this->manager->on('POST', fake_manager::CHECKS, 202, $run);
        $this->setUser($this->student);

        $result = $this->start_check();

        $this->assertCount(1, $this->manager->requests_to('POST', fake_manager::CHECKS));
        $this->assertTrue($result['runs'][0]['outdated']);
        $this->assertFalse($result['runs'][0]['trial']);
    }

    public function test_checks_that_do_not_fit_the_lab_refuse_a_check(): void {
        $session = fake_manager::session();
        $session['checks_def'] = null;
        $session['checks_source'] = 'activity';
        $session['checks_error'] = ['code' => 'checks_invalid', 'problems' => []];
        $this->manager->on('GET', '/api/v1/sessions', 200, $session);
        $this->manager->on('PUT', fake_manager::CHECKS_DEFINITION, 422, ['error' => 'checks_invalid', 'message' => 'no']);
        $this->setUser($this->student);

        try {
            $this->start_check();
            $this->fail('the check started');
        } catch (\moodle_exception $e) {
            $this->assertSame('checksinvalid', $e->errorcode);
        }
        $this->assertSame([], $this->manager->requests_to('POST', fake_manager::CHECKS));
        $state = session_data::export((object) $session);
        $this->assertTrue($state['checkserror']);
    }

    public function test_a_teacher_trial_run_spends_no_attempt_and_gives_no_grade(): void {
        global $CFG;
        require_once($CFG->libdir . '/gradelib.php');
        $this->setUser($this->teacher);

        $result = $this->start_check();
        $done = fake_manager::check_run('done', 'graded', 4);
        $this->manager->on('GET', fake_manager::CHECKS . '/' . fake_manager::RUN, 200, $done);
        get_check_run::execute($this->lab->cmid, fake_manager::RUN, 0);
        $after = external_api::clean_returnvalue(get_checks::execute_returns(), get_checks::execute($this->lab->cmid, 0));

        $this->assertTrue($result['runs'][0]['trial']);
        $this->assertSame(['max' => 1, 'used' => 0, 'left' => 1], $after['attempts']);
        $this->assertTrue($after['canstart']);
        $grades = grade_get_grades($this->course->id, 'mod', 'gns3lab', $this->lab->id, $this->teacher->id);
        $this->assertNull($grades->items[0]->grades[$this->teacher->id]->grade);
    }

    public function test_an_edit_of_the_checks_reaches_the_live_labs(): void {
        global $CFG;
        require_once($CFG->dirroot . '/mod/gns3lab/lib.php');
        $live = fake_manager::session();
        $live['user_id'] = (int) $this->student->id;
        $stopped = fake_manager::session('stopped');
        $stopped['session_id'] = 's-0000000000000001';
        $this->manager->on('GET', '/api/v1/sessions?instance_id=' . $this->lab->id, 200, [$live, $stopped]);
        $this->setAdminUser();
        $cm = get_coursemodule_from_id('gns3lab', $this->lab->cmid);
        $data = (object) ['instance' => $this->lab->id, 'coursemodule' => $cm->id, 'name' => 'Lab', 'labref' => 'cisco-3x2',
            'checksdef' => '{"format":"gns3lab.checks/1","network":"auto"}'];

        gns3lab_update_instance($data, null);
        $this->runAdhocTasks(\mod_gns3lab\task\push_checks_definition::class);

        $puts = $this->manager->requests_to('PUT', fake_manager::CHECKS_DEFINITION);
        $this->assertCount(1, $puts);
        $this->assertSame('auto', $puts[0]['body']['checks']['network']);
        $this->assertSame([], $this->manager->requests_to('PUT', '/api/v1/sessions/s-0000000000000001/checks-definition'));
        $this->assertSame('sha256:cccc', $this->hash()); // Asked of the lab manager: no form checked it here
    }

    /**
     * The stored hash of the checks of the activity.
     *
     * @return string|null
     */
    protected function hash(): ?string {
        global $DB;
        return $DB->get_field('gns3lab', 'checksdefhash', ['id' => $this->lab->id]);
    }

    public function test_the_form_asks_the_lab_manager_about_the_document(): void {
        $this->setUser($this->teacher);

        $ok = external_api::clean_returnvalue(
            validate_checks::execute_returns(),
            validate_checks::execute((int) $this->course->id, 0, 'cisco-3x2', self::DOC)
        );
        $this->manager->on('POST', '/api/v1/checks/validate', 422, ['error' => 'checks_invalid', 'message' => 'no',
            'problems' => [['path' => 'checks.scripts[0].node', 'code' => 'unknown_node', 'message' => 'PC9']]]);
        $bad = external_api::clean_returnvalue(
            validate_checks::execute_returns(),
            validate_checks::execute((int) $this->course->id, 0, 'cisco-3x2', self::DOC)
        );
        $nolab = validate_checks::execute((int) $this->course->id, 0, '', self::DOC);

        $this->assertTrue($ok['valid']);
        $this->assertSame('sha256:cccc', $ok['checksdef']);
        [$post] = $this->manager->requests_to('POST', '/api/v1/checks/validate');
        $this->assertSame('cisco-3x2', $post['body']['lab_ref']);
        $this->assertFalse($bad['valid']);
        $this->assertSame('checks.scripts[0].node', $bad['problems'][0]['path']);
        $this->assertNotSame('', $nolab['error']);
    }

    public function test_a_student_may_not_ask_about_the_checks(): void {
        $this->setUser($this->student);
        $this->expectException(\required_capability_exception::class);
        validate_checks::execute((int) $this->course->id, 0, 'cisco-3x2', self::DOC);
    }

    public function test_the_devices_of_a_library_lab_for_the_block(): void {
        global $DB;
        $this->setAdminUser();
        $this->manager->on('GET', '/api/v1/images', 200, ['images' => [fake_manager::image(), fake_manager::host_image()]]);
        $this->manager->with_info(['images', 'lab_definitions', 'activity-checks']);
        $labid = lab_library::create_lab((object) ['name' => 'Ring']);
        $def = fake_manager::lab();
        $def['checks'] = ['format' => 'gns3lab.checks/1', 'network' => 'off'];
        $version = lab_library::save_draft($labid, \mod_gns3lab\local\lab_definition::from_json(json_encode($def), 'Ring'));
        $DB->set_field('gns3lab_lab_versions', 'status', lab_library::PUBLISHED, ['id' => $version->id]);

        $ctx = external_api::clean_returnvalue(
            get_checks_lab::execute_returns(),
            get_checks_lab::execute((int) $this->course->id, (int) $version->id, '')
        );

        $this->assertTrue($ctx['found']);
        $this->assertFalse($ctx['catalog']);
        $this->assertSame(array_column($def['nodes'], 'name'), array_column($ctx['nodes'], 'name'));
        $this->assertSame(['format' => 'gns3lab.checks/1', 'network' => 'off'], json_decode($ctx['labchecks'], true));
    }
}
