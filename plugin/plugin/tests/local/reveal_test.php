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
 * Tests of the settings of the modes seen and fog and of the names of the nodes of a lab.
 *
 * @package    mod_gns3lab
 * @category   test
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
#[\PHPUnit\Framework\Attributes\CoversClass(\mod_gns3lab\local\reveal::class)]
final class reveal_test extends \advanced_testcase {
    public function test_the_mode_is_one_of_three_and_live_by_default(): void {
        $this->assertSame('fog', reveal::mode((object) ['factsview' => 'fog']));
        $this->assertSame('seen', reveal::mode((object) ['factsview' => 'seen']));
        $this->assertSame('live', reveal::mode((object) ['factsview' => 'live']));
        $this->assertSame('live', reveal::mode((object) ['factsview' => 'nonsense']));
        $this->assertSame('live', reveal::mode((object) []));
    }

    public function test_names_come_as_a_json_list_or_as_a_text(): void {
        $this->assertSame(['R1', 'PC 1'], reveal::parse_names('["R1","PC 1"]'));
        $this->assertSame(['R1', 'PC1', 'SW-1'], reveal::parse_names(" R1, PC1\n SW-1,, R1 "));
        $this->assertSame([], reveal::parse_names(''));
        $this->assertSame([], reveal::parse_names(null));
        $this->assertSame('["R1","PC1"]', reveal::encode_names([' R1 ', 'PC1', '', 'R1']));
        $this->assertSame('', reveal::encode_names([]));
    }

    public function test_the_starting_nodes_matter_in_a_fog_only(): void {
        $this->assertSame(['R1'], reveal::start_names((object) ['factsview' => 'fog', 'fogstart' => '["R1"]']));
        $this->assertSame([], reveal::start_names((object) ['factsview' => 'seen', 'fogstart' => '["R1"]']));
        $this->assertSame([], reveal::start_names((object) ['factsview' => 'fog', 'fogstart' => '']));
    }

    public function test_the_activity_keeps_the_settings_the_form_sends(): void {
        global $DB;
        $this->resetAfterTest();
        $gen = $this->getDataGenerator();
        $course = $gen->create_course();

        $fog = $gen->create_module('gns3lab', ['course' => $course->id, 'factsview' => 'fog', 'fogstart' => ['R1', 'PC1', 'R1']]);
        $seen = $gen->create_module('gns3lab', ['course' => $course->id, 'factsview' => 'seen', 'fogstart' => ['R1']]);
        $odd = $gen->create_module('gns3lab', ['course' => $course->id, 'factsview' => 'odd']);
        $plain = $gen->create_module('gns3lab', ['course' => $course->id]);

        $this->assertSame('fog', $DB->get_field('gns3lab', 'factsview', ['id' => $fog->id]));
        $this->assertSame('["R1","PC1"]', $DB->get_field('gns3lab', 'fogstart', ['id' => $fog->id]));
        // Starting nodes make sense in a fog only.
        $this->assertSame('seen', $DB->get_field('gns3lab', 'factsview', ['id' => $seen->id]));
        $this->assertEmpty($DB->get_field('gns3lab', 'fogstart', ['id' => $seen->id]));
        $this->assertSame('live', $DB->get_field('gns3lab', 'factsview', ['id' => $odd->id]));
        $this->assertSame('live', $DB->get_field('gns3lab', 'factsview', ['id' => $plain->id]));
    }

    public function test_deleting_the_activity_deletes_what_students_uncovered_in_it(): void {
        global $DB;
        $this->resetAfterTest();
        $gen = $this->getDataGenerator();
        $course = $gen->create_course();
        $one = $gen->create_module('gns3lab', ['course' => $course->id, 'factsview' => 'fog']);
        $two = $gen->create_module('gns3lab', ['course' => $course->id, 'factsview' => 'fog']);
        $student = $gen->create_and_enrol($course, 'student');
        foreach ([$one, $two] as $lab) {
            $DB->insert_record('gns3lab_reveal', (object) [
                'gns3labid' => $lab->id, 'userid' => $student->id, 'nodeid' => 'n', 'kind' => 'node', 'item' => '',
                'timecreated' => time(),
            ]);
        }

        course_delete_module($one->cmid);

        $this->assertSame(0, $DB->count_records('gns3lab_reveal', ['gns3labid' => $one->id]));
        $this->assertSame(1, $DB->count_records('gns3lab_reveal', ['gns3labid' => $two->id]));
    }

    public function test_the_names_of_the_nodes_of_a_reference_lab_and_of_a_library_lab(): void {
        $this->resetAfterTest();
        $this->setAdminUser();
        $manager = new fake_manager();
        $manager->install();
        $manager->with_lab_definitions();
        $manager->on('GET', '/api/v1/labs/cisco-3x2/topology', 200, [
            'name' => 'cisco-3x2',
            'topology' => ['nodes' => [['node_id' => 'a', 'name' => 'R1'], ['node_id' => 'b', 'name' => 'SW1'], ['node_id' => 'c']],
                'links' => [], 'drawings' => []],
        ]);

        $this->assertSame(['R1', 'SW1'], reveal::lab_node_names((object) ['labversionid' => 0, 'labref' => 'cisco-3x2']));
        // Nothing to read: no lab, a lab the manager does not show.
        $this->assertNull(reveal::lab_node_names((object) ['labversionid' => 0, 'labref' => '']));
        $manager->on('GET', '/api/v1/labs/gone/topology', 404, ['error' => 'lab_not_found', 'message' => 'No such lab']);
        $this->assertNull(reveal::lab_node_names((object) ['labversionid' => 0, 'labref' => 'gone']));
        $this->assertDebuggingCalled();

        $labid = lab_library::create_lab((object) ['name' => 'OSPF', 'description' => '', 'tags' => []]);
        $def = lab_definition::from_json(json_encode(fake_manager::lab()), 'OSPF');
        $version = lab_library::publish($labid, $def);
        $names = reveal::lab_node_names((object) ['labversionid' => $version->id, 'labref' => '']);
        $this->assertSame(array_column($def['nodes'], 'name'), $names);
    }
}
