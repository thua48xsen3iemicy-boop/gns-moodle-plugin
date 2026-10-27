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
use mod_gns3lab\local\lab_library;
use mod_gns3lab\tests\fake_manager;

/**
 * Tests of the lab editor functions: mod_gns3lab_save_lab_draft, mod_gns3lab_validate_lab, mod_gns3lab_publish_lab.
 *
 * @package    mod_gns3lab
 * @category   test
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
#[\PHPUnit\Framework\Attributes\CoversClass(\mod_gns3lab\external\save_lab_draft::class)]
#[\PHPUnit\Framework\Attributes\CoversClass(\mod_gns3lab\external\validate_lab::class)]
#[\PHPUnit\Framework\Attributes\CoversClass(\mod_gns3lab\external\publish_lab::class)]
#[\PHPUnit\Framework\Attributes\CoversClass(\mod_gns3lab\external\lab_problems::class)]
final class lab_editor_test extends \advanced_testcase {
    /** @var fake_manager */
    protected fake_manager $manager;

    /** @var \stdClass user with the lab author role */
    protected \stdClass $author;

    /** @var int lab id */
    protected int $labid;

    protected function setUp(): void {
        global $DB;
        parent::setUp();
        $this->resetAfterTest();
        $this->manager = new fake_manager();
        $this->manager->install();
        $this->manager->with_lab_definitions();
        $this->author = $this->getDataGenerator()->create_user();
        $roleid = $DB->get_field('role', 'id', ['shortname' => lab_library::AUTHOR_ROLE], MUST_EXIST);
        role_assign($roleid, $this->author->id, \context_system::instance());
        $this->setUser($this->author);
        $this->labid = lab_library::create_lab((object) ['name' => 'OSPF']);
    }

    public function test_author_saves_and_publishes(): void {
        $json = json_encode(fake_manager::lab());

        $saved = external_api::clean_returnvalue(
            save_lab_draft::execute_returns(),
            save_lab_draft::execute($this->labid, $json)
        );
        $published = external_api::clean_returnvalue(
            publish_lab::execute_returns(),
            publish_lab::execute($this->labid, $json)
        );

        $this->assertSame(1, $saved['version']);
        $this->assertSame(['published' => true, 'version' => 1, 'problems' => []], $published);
        $this->assertSame('sha256:aaaa', lab_library::last_published($this->labid)->labdef);
    }

    public function test_validate_reports_the_problems_of_the_manager(): void {
        $this->manager->on('POST', '/api/v1/labs/validate', 422, [
            'error' => 'lab_invalid',
            'message' => '1 problem',
            'problems' => [['path' => 'links[0].b', 'code' => 'port_in_use', 'message' => 'R2 1/0 already linked']],
        ]);

        $result = external_api::clean_returnvalue(
            validate_lab::execute_returns(),
            validate_lab::execute($this->labid, json_encode(fake_manager::lab()))
        );

        $this->assertFalse($result['valid']);
        $this->assertSame(
            [['path' => 'links[0].b', 'code' => 'port_in_use', 'message' => 'R2 1/0 already linked']],
            $result['problems']
        );
        // Validation saves nothing.
        $this->assertSame([], json_decode(lab_library::latest_version($this->labid)->definition, true)['nodes']);
    }

    public function test_validate_gives_the_resources(): void {
        $result = validate_lab::execute($this->labid, json_encode(fake_manager::lab()));

        $this->assertSame(
            ['valid' => true, 'memorymb' => 3072, 'cores' => 2, 'problems' => [], 'warnings' => []],
            $result
        );
    }

    public function test_a_lab_with_segments_is_saved_as_lab_2_and_validate_gives_the_warnings(): void {
        $this->manager->with_lab_constructor();
        $json = json_encode(fake_manager::lab_with_segments());

        $result = external_api::clean_returnvalue(
            validate_lab::execute_returns(),
            validate_lab::execute($this->labid, $json)
        );
        save_lab_draft::execute($this->labid, $json);

        $this->assertTrue($result['valid']);
        $this->assertSame(
            [['path' => 'segments[0].gateway', 'code' => 'gateway_not_configured', 'message' => 'by the student']],
            $result['warnings']
        );
        [$request] = $this->manager->requests_to('POST', '/api/v1/labs/validate');
        $this->assertSame('gns3lab.lab/2', $request['body']['lab']['format']);
        $this->assertSame('LAN1', $request['body']['lab']['segments'][0]['name']);
        $stored = json_decode(lab_library::latest_version($this->labid)->definition, true);
        $this->assertSame('gns3lab.lab/2', $stored['format']);
        $this->assertSame(20, $stored['segments'][0]['members'][1]['host']);
    }

    public function test_the_problems_of_segments_reach_the_editor(): void {
        $this->manager->with_lab_constructor();
        $this->manager->on('POST', '/api/v1/labs/validate', 422, [
            'error' => 'lab_invalid',
            'message' => '1 problem',
            'problems' => [['path' => 'segments[0].members[1]', 'code' => 'host_in_use', 'message' => 'host 20']],
        ]);

        $result = validate_lab::execute($this->labid, json_encode(fake_manager::lab_with_segments()));

        $this->assertFalse($result['valid']);
        $this->assertSame('host_in_use', $result['problems'][0]['code']);
        $this->assertSame([], $result['warnings']);
    }

    public function test_unreachable_manager_is_an_error_not_a_problem(): void {
        $this->manager->on('POST', '/api/v1/labs/validate', 503, ['error' => 'overloaded', 'message' => 'later']);

        $this->expectException(\mod_gns3lab\local\manager_exception::class);
        validate_lab::execute($this->labid, json_encode(fake_manager::lab()));
    }

    public function test_an_empty_lab_is_not_published(): void {
        $this->expectExceptionMessage(get_string('labempty', 'gns3lab'));
        publish_lab::execute($this->labid, json_encode(['format' => 'gns3lab.lab/1', 'nodes' => [], 'links' => []]));
    }

    public function test_teachers_cannot_edit_the_library(): void {
        $course = $this->getDataGenerator()->create_course();
        $teacher = $this->getDataGenerator()->create_and_enrol($course, 'editingteacher');
        $this->setUser($teacher);

        $this->expectException(\required_capability_exception::class);
        save_lab_draft::execute($this->labid, json_encode(fake_manager::lab()));
    }
}
