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
 * Tests of the site lab library.
 *
 * @package    mod_gns3lab
 * @category   test
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
#[\PHPUnit\Framework\Attributes\CoversClass(\mod_gns3lab\local\lab_library::class)]
final class lab_library_test extends \advanced_testcase {
    /** @var fake_manager */
    protected fake_manager $manager;

    protected function setUp(): void {
        parent::setUp();
        $this->resetAfterTest();
        $this->setAdminUser();
        $this->manager = new fake_manager();
        $this->manager->install();
        $this->manager->with_lab_definitions();
    }

    /**
     * A lab with its definition, the way the editor saves it.
     *
     * @param string $name lab name
     * @param array $tags tags
     * @return array lab id and clean definition
     */
    protected function lab(string $name = 'OSPF', array $tags = []): array {
        $labid = lab_library::create_lab((object) ['name' => $name, 'description' => '', 'tags' => $tags]);
        return [$labid, lab_definition::from_json(json_encode(fake_manager::lab()), $name)];
    }

    public function test_author_role_is_created_on_install(): void {
        global $DB;

        $roleid = $DB->get_field('role', 'id', ['shortname' => lab_library::AUTHOR_ROLE], MUST_EXIST);
        $author = $this->getDataGenerator()->create_user();
        role_assign($roleid, $author->id, \context_system::instance());

        $this->assertTrue(has_capability('mod/gns3lab:managelabs', \context_system::instance(), $author));
        $this->assertSame([CONTEXT_SYSTEM], array_values(array_map('intval', get_role_contextlevels($roleid))));
        $nobody = $this->getDataGenerator()->create_user();
        $this->assertFalse(has_capability('mod/gns3lab:managelabs', \context_system::instance(), $nobody));
    }

    public function test_a_new_lab_has_an_empty_draft(): void {
        [$labid] = $this->lab();

        $draft = lab_library::latest_version($labid);

        $this->assertSame(lab_library::DRAFT, $draft->status);
        $this->assertSame(1, (int) $draft->version);
        $this->assertSame([], lab_definition::decode($draft->definition)['nodes']);
        $this->assertNull(lab_library::last_published($labid));
    }

    public function test_publishing_freezes_the_draft_with_the_manager_hash(): void {
        [$labid, $def] = $this->lab();

        $version = lab_library::publish($labid, $def);

        $this->assertSame(lab_library::PUBLISHED, $version->status);
        $this->assertSame(1, (int) $version->version);
        $this->assertSame('sha256:aaaa', $version->labdef);
        $this->assertSame(3072, (int) $version->memorymb);
        [$request] = $this->manager->requests_to('POST', '/api/v1/labs/validate');
        $this->assertSame('OSPF', $request['body']['lab']['title']);
        $this->assertSame('cisco-iou-l3', $request['body']['lab']['nodes'][0]['image']);

        // Editing after publishing starts the draft of the next version; the published one stays.
        $draft = lab_library::save_draft($labid, $def);
        $this->assertSame(2, (int) $draft->version);
        $this->assertSame(lab_library::PUBLISHED, lab_library::get_version((int) $version->id)->status);
    }

    public function test_publishing_the_same_lab_again_keeps_the_version(): void {
        [$labid, $def] = $this->lab();
        $first = lab_library::publish($labid, $def);

        $again = lab_library::publish($labid, $def);

        $this->assertEquals($first->id, $again->id);
        $this->assertSame(lab_library::PUBLISHED, lab_library::latest_version($labid)->status);
    }

    public function test_problems_keep_the_draft(): void {
        [$labid, $def] = $this->lab();
        $this->manager->on('POST', '/api/v1/labs/validate', 422, [
            'error' => 'lab_invalid',
            'message' => '1 problem',
            'problems' => [['path' => 'nodes[1].ram_mb', 'code' => 'out_of_range', 'message' => '512..2048']],
        ]);

        try {
            lab_library::publish($labid, $def);
            $this->fail('No exception');
        } catch (manager_exception $e) {
            $this->assertSame('lab_invalid', $e->remotecode);
            $this->assertSame('nodes[1].ram_mb', $e->details->problems[0]->path);
        }
        $draft = lab_library::latest_version($labid);
        $this->assertSame(lab_library::DRAFT, $draft->status);
        $this->assertCount(2, lab_definition::decode($draft->definition)['nodes']);
    }

    public function test_a_lab_in_use_cannot_be_deleted(): void {
        global $DB;
        [$labid, $def] = $this->lab();
        $version = lab_library::publish($labid, $def);
        $course = $this->getDataGenerator()->create_course();
        $activity = $this->getDataGenerator()->create_module('gns3lab', ['course' => $course->id, 'labversionid' => $version->id]);

        $this->assertSame([$labid => 1], lab_library::usage([$labid]));
        try {
            lab_library::delete_lab($labid);
            $this->fail('No exception');
        } catch (\moodle_exception $e) {
            $this->assertSame('labinuse', $e->errorcode);
        }

        course_delete_module($activity->cmid);
        lab_library::delete_lab($labid);
        $this->assertFalse($DB->record_exists('gns3lab_lab_versions', ['labid' => $labid]));
    }

    public function test_list_by_tag_and_menu_of_published_versions(): void {
        [$ospf, $def] = $this->lab('OSPF', ['ospf', 'routing']);
        $this->lab('VLAN', ['vlan']);
        lab_library::publish($ospf, $def);

        $this->assertSame(['OSPF', 'VLAN'], array_column(lab_library::list_labs(), 'name'));
        $labs = lab_library::list_labs('Routing');
        $this->assertSame(['OSPF'], array_column($labs, 'name'));
        $this->assertSame(['ospf', 'routing'], $labs[0]->tags);
        $this->assertSame(1, (int) $labs[0]->published->version);
        $this->assertSame(['OSPF (version 1)'], array_values(lab_library::published_menu()));
    }

    public function test_activity_lab(): void {
        [$labid, $def] = $this->lab();
        $version = lab_library::publish($labid, $def);

        $this->assertSame('cisco-3x2', lab_library::activity_lab((object) ['labversionid' => 0, 'labref' => ' cisco-3x2 ']));
        $this->assertNull(lab_library::activity_lab((object) ['labversionid' => 0, 'labref' => '']));
        $def = lab_library::activity_lab((object) ['labversionid' => $version->id, 'labref' => '']);
        $this->assertSame('R1', $def['nodes'][0]['name']);
        // A draft is never run.
        $draft = lab_library::save_draft($labid, $def);
        $this->assertNull(lab_library::activity_lab((object) ['labversionid' => $draft->id, 'labref' => '']));
    }
}
