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

use mod_gns3lab\tests\course_actions;
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

    public function test_a_change_the_manager_does_not_see_still_makes_a_version(): void {
        [$labid, $def] = $this->lab();
        $first = lab_library::publish($labid, $def);

        $def['annotations'] = [['kind' => 'text', 'x' => 0, 'y' => 0, 'text' => 'Office A', 'size' => 14]];
        $second = lab_library::publish($labid, $def);

        $this->assertNotEquals($first->id, $second->id);
        $this->assertSame(2, (int) $second->version);
        $this->assertSame('Office A', lab_definition::decode($second->definition)['annotations'][0]['text']);
        $requests = $this->manager->requests_to('POST', '/api/v1/labs/validate');
        $this->assertArrayNotHasKey('annotations', end($requests)['body']['lab']);
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

        course_actions::delete_module($activity->cmid);
        lab_library::delete_lab($labid);
        $this->assertFalse($DB->record_exists('gns3lab_lab_versions', ['labid' => $labid]));
    }

    public function test_list_by_tag(): void {
        [$ospf, $def] = $this->lab('OSPF', ['ospf', 'routing']);
        $this->lab('VLAN', ['vlan']);
        lab_library::publish($ospf, $def);

        $this->assertSame(['OSPF', 'VLAN'], array_column(lab_library::list_labs(), 'name'));
        $labs = lab_library::list_labs('Routing');
        $this->assertSame(['OSPF'], array_column($labs, 'name'));
        $this->assertSame(['ospf', 'routing'], $labs[0]->tags);
        $this->assertSame(1, (int) $labs[0]->published->version);
    }

    public function test_activity_menu_groups_latest_versions_by_date(): void {
        global $DB;
        $teacher = $this->getDataGenerator()->create_user(['firstname' => 'Tom', 'lastname' => 'Teacher']);
        $other = $this->getDataGenerator()->create_user(['firstname' => 'Olga', 'lastname' => 'Other']);
        $course = $this->getDataGenerator()->create_course();

        $this->setUser($teacher);
        [$ospf, $def] = $this->lab('OSPF');
        $ospf1 = lab_library::publish($ospf, $def);
        $this->manager->with_lab_definitions('sha256:bbbb');
        $ospf2 = lab_library::publish($ospf, $def);
        $this->lab('Draft only');

        $this->setUser($other);
        [$vlan, $def] = $this->lab('VLAN');
        $vlan1 = lab_library::publish($vlan, $def);
        [$bgp, $def] = $this->lab('BGP');
        $bgp1 = lab_library::publish($bgp, $def);
        [$stp, $def] = $this->lab('STP');
        $stp1 = lab_library::publish($stp, $def);
        // A co-author: the teacher edits a lab of another author, it becomes theirs too.
        [$nat, $def] = $this->lab('NAT');
        $this->setUser($teacher);
        $nat1 = lab_library::publish($nat, $def);
        $this->getDataGenerator()->create_module('gns3lab', ['course' => $course->id, 'labversionid' => $bgp1->id]);

        $dates = [$ospf1->id => 100, $ospf2->id => 400, $nat1->id => 300, $vlan1->id => 200, $bgp1->id => 100, $stp1->id => 500];
        foreach ($dates as $id => $time) {
            $DB->set_field('gns3lab_lab_versions', 'timepublished', $time, ['id' => $id]);
        }

        $menu = lab_library::activity_menu((int) $teacher->id, (int) $course->id);
        $this->assertSame(['mine', 'course', 'others'], array_keys($menu));
        // One entry per lab at its latest version, the newest publication first.
        $this->assertSame([(int) $ospf2->id, (int) $nat1->id], array_keys($menu['mine']));
        $this->assertSame([(int) $bgp1->id], array_keys($menu['course']));
        $this->assertSame([(int) $stp1->id, (int) $vlan1->id], array_keys($menu['others']));
        $this->assertStringStartsWith('OSPF · v2 · ', $menu['mine'][$ospf2->id]);
        $this->assertStringNotContainsString('Teacher', $menu['mine'][$ospf2->id]);
        $this->assertStringStartsWith('BGP · v1 · Olga Other · ', $menu['course'][$bgp1->id]);

        // The version an activity runs stays in the menu, right after the latest one.
        $menu = lab_library::activity_menu((int) $teacher->id, (int) $course->id, (int) $ospf1->id);
        $this->assertSame([(int) $ospf2->id, (int) $ospf1->id, (int) $nat1->id], array_keys($menu['mine']));
        $this->assertStringStartsWith('OSPF · v1 · ', $menu['mine'][$ospf1->id]);
        $this->assertStringEndsWith(')', $menu['mine'][$ospf1->id]);

        // Another teacher sees the same labs from their side: their own labs first, no course group without activities.
        $menu = lab_library::activity_menu((int) $other->id, (int) $this->getDataGenerator()->create_course()->id);
        $this->assertSame(['mine', 'others'], array_keys($menu));
        $this->assertSame([(int) $stp1->id, (int) $nat1->id, (int) $vlan1->id, (int) $bgp1->id], array_keys($menu['mine']));
        $this->assertStringNotContainsString('Draft only', implode(' ', array_merge(...array_values($menu))));

        $this->assertTrue(lab_library::is_published((int) $ospf1->id));
        $this->assertFalse(lab_library::is_published((int) lab_library::save_draft($ospf, $def)->id));
        $this->assertFalse(lab_library::is_published(0));
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
