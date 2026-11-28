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
use mod_gns3lab\local\checks_lab;
use mod_gns3lab\local\lab_definition;
use mod_gns3lab\local\lab_library;
use mod_gns3lab\local\setup_prompt;
use mod_gns3lab\tests\fake_manager;

/**
 * Tests of the teacher's setup steps of a lab (docs/lab-setup/README.md, plugin docs/lab-setup.md): the definition keeps
 * them, the lab manager checks them, SETUP_FAILED tells the step only to the author and observers, the checks of an
 * activity know the steps of its lab.
 *
 * @package    mod_gns3lab
 * @category   test
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
#[\PHPUnit\Framework\Attributes\CoversClass(\mod_gns3lab\local\lab_definition::class)]
#[\PHPUnit\Framework\Attributes\CoversClass(\mod_gns3lab\local\session_data::class)]
#[\PHPUnit\Framework\Attributes\CoversClass(\mod_gns3lab\local\checks_lab::class)]
#[\PHPUnit\Framework\Attributes\CoversClass(\mod_gns3lab\local\setup_prompt::class)]
#[\PHPUnit\Framework\Attributes\CoversClass(\mod_gns3lab\external\get_state::class)]
#[\PHPUnit\Framework\Attributes\CoversClass(\mod_gns3lab\external\validate_lab::class)]
#[\PHPUnit\Framework\Attributes\CoversClass(\mod_gns3lab\external\authoring::class)]
final class lab_setup_test extends \advanced_testcase {
    /** @var fake_manager */
    protected fake_manager $manager;

    protected function setUp(): void {
        parent::setUp();
        $this->resetAfterTest();
        $this->manager = new fake_manager();
        $this->manager->install();
    }

    /**
     * Setup steps as the editor sends them.
     *
     * @return array
     */
    protected static function steps(): array {
        return [
            ['id' => 'web', 'title' => 'nginx на PC1', 'node' => 'PC1', 'timeout' => 90, 'run' => 'apt-get install -y nginx'],
            ['id' => 'ospf', 'title' => 'OSPF на R1', 'node' => 'R1',
                'config' => "router ospf 1\n network 0.0.0.0 0.0.0.0 area 0"],
        ];
    }

    /**
     * A failed session whose setup step failed (manager CONTRACTS §2.3, §2.5).
     *
     * @return array
     */
    protected static function failed_session(): array {
        $session = fake_manager::session('failed');
        $session['error'] = ['code' => 'SETUP_FAILED', 'retryable' => false];
        $session['setup_error'] = ['step' => 'web', 'node' => 'PC1', 'exit_code' => 100,
            'message' => 'E: Unable to locate package <nginx>'];
        return $session;
    }

    public function test_the_definition_keeps_the_steps_and_becomes_lab_2(): void {
        $raw = fake_manager::lab();
        $raw['nodes'][] = ['name' => 'PC1', 'image' => 'debian12', 'x' => 0, 'y' => 100];
        $raw['setup'] = self::steps();
        $raw['setup'][] = [];

        $def = lab_definition::from_json(json_encode($raw), 'Setup');

        $this->assertSame(lab_definition::FORMAT_2, $def['format']);
        $this->assertSame([], $def['segments']);
        $this->assertSame(self::steps(), array_slice($def['setup'], 0, 2));
        // An empty step goes to the lab manager as an object: it answers "required", not "bad_type".
        $this->assertInstanceOf(\stdClass::class, $def['setup'][2]);
        $this->assertStringContainsString('"setup":[{"id":"web"', json_encode(lab_definition::for_manager($def)));
        // The steps are part of the lab: a version without the checks keeps them, and stays lab/2.
        $def['checks'] = ['format' => lab_definition::CHECKS_FORMAT];
        $without = lab_definition::without_checks($def);
        $this->assertArrayNotHasKey('checks', $without);
        $this->assertSame(lab_definition::FORMAT_2, $without['format']);
        $this->assertCount(3, $without['setup']);
        // An empty list is no field: the lab stays lab/1.
        $raw['setup'] = [];
        $this->assertSame(lab_definition::FORMAT, lab_definition::from_json(json_encode($raw), 'Setup')['format']);
    }

    /**
     * Setup fields the editor never sends.
     *
     * @return array
     */
    public static function broken_setup_provider(): array {
        $big = [['id' => 'big', 'title' => 'x', 'node' => 'R1', 'config' => str_repeat('x', lab_definition::SETUP_MAX)]];
        return [
            'an object' => [['web' => ['node' => 'R1']]],
            'a string' => ['web'],
            'a step as a list' => [[['web', 'R1']]],
            'a step as a string' => [['web']],
            'too large' => [$big],
        ];
    }

    /**
     * A setup of the wrong shape or size is refused before anything is stored or sent.
     *
     * @param mixed $setup broken setup
     */
    #[\PHPUnit\Framework\Attributes\DataProvider('broken_setup_provider')]
    public function test_refuses_a_broken_setup(mixed $setup): void {
        $raw = fake_manager::lab();
        $raw['setup'] = $setup;
        $this->expectException(\moodle_exception::class);
        lab_definition::from_json(json_encode($raw), 'Setup');
    }

    public function test_the_lab_manager_checks_the_steps_and_its_problems_reach_the_editor(): void {
        global $DB;
        $this->manager->with_lab_constructor();
        $this->manager->with_info(['consoles', 'images', 'lab_definitions', 'lab_constructor', 'lab-setup'],
            ['lab_formats' => ['gns3lab.lab/1', 'gns3lab.lab/2'], 'limits' => ['max_nodes' => 30]]);
        $this->manager->on('POST', '/api/v1/labs/validate', 422, ['error' => 'lab_invalid', 'message' => '1 problem',
            'problems' => [['path' => 'setup[0].node', 'code' => 'setup_node_unsupported', 'message' => 'no guest agent']]]);
        $author = $this->getDataGenerator()->create_user();
        $role = $DB->get_field('role', 'id', ['shortname' => lab_library::AUTHOR_ROLE], MUST_EXIST);
        role_assign($role, $author->id, \context_system::instance());
        $this->setUser($author);
        $labid = lab_library::create_lab((object) ['name' => 'Setup']);
        $raw = fake_manager::lab();
        $raw['setup'] = [self::steps()[1]];

        $result = external_api::clean_returnvalue(validate_lab::execute_returns(),
            validate_lab::execute($labid, json_encode($raw)));

        $this->assertTrue(\core\di::get(\mod_gns3lab\local\manager_client::class)->supports_lab_setup());
        $this->assertFalse($result['valid']);
        $this->assertSame([['path' => 'setup[0].node', 'code' => 'setup_node_unsupported', 'message' => 'no guest agent']],
            $result['problems']);
        [$request] = $this->manager->requests_to('POST', '/api/v1/labs/validate');
        $this->assertSame('gns3lab.lab/2', $request['body']['lab']['format']);
        $this->assertSame([], $request['body']['lab']['segments']);
        $this->assertSame('ospf', $request['body']['lab']['setup'][0]['id']);
    }

    public function test_the_student_gets_no_step_of_a_failed_setup(): void {
        $gen = $this->getDataGenerator();
        $course = $gen->create_course();
        $lab = $gen->create_module('gns3lab', ['course' => $course->id]);
        $student = $gen->create_and_enrol($course, 'student');
        $this->manager->on('GET', '/api/v1/sessions', 200, self::failed_session());
        $this->setUser($student);

        $state = external_api::clean_returnvalue(get_state::execute_returns(), get_state::execute($lab->cmid));

        $this->assertSame('SETUP_FAILED', $state['session']['errorcode']);
        $this->assertFalse($state['session']['errorretryable']);
        $this->assertArrayNotHasKey('setuperror', $state['session']);
    }

    public function test_an_observer_sees_the_step_that_failed(): void {
        $gen = $this->getDataGenerator();
        $course = $gen->create_course();
        $lab = $gen->create_module('gns3lab', ['course' => $course->id]);
        $student = $gen->create_and_enrol($course, 'student');
        $teacher = $gen->create_and_enrol($course, 'editingteacher');
        $this->manager->on('GET', '/api/v1/sessions', 200, self::failed_session());
        $this->setUser($teacher);

        $state = external_api::clean_returnvalue(get_state::execute_returns(),
            get_state::execute($lab->cmid, (int) $student->id));

        $this->assertSame(['step' => 'web', 'node' => 'PC1', 'exitcode' => 100,
            'message' => 'E: Unable to locate package <nginx>'], $state['session']['setuperror']);

        // Another failure has no step, even for an observer.
        $other = fake_manager::session('failed');
        $other['error'] = ['code' => 'SLOT_OPERATION_FAILED', 'retryable' => true];
        $this->manager->on('GET', '/api/v1/sessions', 200, $other);
        $state = external_api::clean_returnvalue(get_state::execute_returns(),
            get_state::execute($lab->cmid, (int) $student->id));
        $this->assertArrayNotHasKey('setuperror', $state['session']);
    }

    public function test_the_author_sees_the_step_in_an_authoring_run(): void {
        global $DB;
        $this->setAdminUser();
        $labid = lab_library::create_lab((object) ['name' => 'Setup']);
        $author = $this->getDataGenerator()->create_user();
        $role = $DB->get_field('role', 'id', ['shortname' => lab_library::AUTHOR_ROLE], MUST_EXIST);
        role_assign($role, $author->id, \context_system::instance());
        $this->setUser($author);
        $this->manager->with_lab_definitions();
        $this->manager->with_info(['images', 'lab_definitions', 'image_capture', 'consoles']);
        $session = self::failed_session();
        $session['mode'] = 'authoring';
        $this->manager->on('GET', '/api/v1/sessions', 200, $session);

        $result = external_api::clean_returnvalue(authoring::execute_returns(), authoring::execute($labid, 'state'));

        $this->assertSame('web', $result['state']['session']['setuperror']['step']);
        $this->assertSame(100, $result['state']['session']['setuperror']['exitcode']);
    }

    public function test_the_checks_of_an_activity_know_the_setup_of_its_lab_without_the_code(): void {
        $this->setAdminUser();
        $this->manager->on('GET', '/api/v1/images', 200, ['images' => [fake_manager::image(), fake_manager::host_image()]]);
        $this->manager->with_info(['images', 'lab_definitions']);
        $raw = fake_manager::lab();
        $raw['nodes'][] = ['name' => 'PC1', 'image' => 'debian12', 'x' => 0, 'y' => 100];
        $raw['setup'] = self::steps();

        $ctx = checks_lab::context(lab_definition::from_json(json_encode($raw), 'Setup'));

        $this->assertSame([
            ['id' => 'web', 'title' => 'nginx на PC1', 'node' => 'PC1'],
            ['id' => 'ospf', 'title' => 'OSPF на R1', 'node' => 'R1'],
        ], $ctx['setup']);
        $exported = external_api::clean_returnvalue(get_checks_lab::execute_returns(), get_checks_lab::export($ctx));
        $this->assertSame('OSPF на R1', $exported['setup'][1]['title']);
        $this->assertStringNotContainsString('apt-get', json_encode($exported));
        $this->assertSame([], checks_lab::context('cisco-3x2')['setup']);
    }

    public function test_the_prompt_of_the_setup_block(): void {
        $text = setup_prompt::text('ru');

        $this->assertStringNotContainsString('<!--', $text);
        foreach (['[[LAB]]', '[[SETUP]]', '[[TASK]]'] as $mark) {
            $this->assertSame(1, substr_count($text, $mark));
        }
        $this->assertSame(setup_prompt::text('en'), setup_prompt::text('xx'));
    }
}
