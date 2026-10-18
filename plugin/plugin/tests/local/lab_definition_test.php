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
 * Tests of the lab definition format gns3lab.lab/1.
 *
 * @package    mod_gns3lab
 * @category   test
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
#[\PHPUnit\Framework\Attributes\CoversClass(\mod_gns3lab\local\lab_definition::class)]
final class lab_definition_test extends \basic_testcase {
    public function test_keeps_known_fields_and_sets_the_title(): void {
        $raw = fake_manager::lab();
        $raw['title'] = 'From the browser';
        $raw['extra'] = 'dropped';
        $raw['nodes'][0]['console'] = 5000;

        $def = lab_definition::from_json(json_encode($raw), 'OSPF');

        $this->assertSame('OSPF', $def['title']);
        $this->assertSame(['format', 'title', 'nodes', 'links'], array_keys($def));
        $this->assertSame(['name' => 'R1', 'image' => 'cisco-iou-l3', 'x' => -100, 'y' => 0, 'adapters' => 2], $def['nodes'][0]);
        $this->assertSame(512, $def['nodes'][1]['ram_mb']);
        $this->assertSame(['node' => 'R2', 'adapter' => 1, 'port' => 0], $def['links'][0]['b']);
    }

    /**
     * Definitions the editor never sends.
     *
     * @return array
     */
    public static function broken_provider(): array {
        $dup = fake_manager::lab();
        $dup['nodes'][1]['name'] = 'R1';
        $name = fake_manager::lab();
        $name['nodes'][0]['name'] = 'R 1';
        $link = fake_manager::lab();
        $link['links'][0]['b']['node'] = 'R9';
        $port = fake_manager::lab();
        $port['links'][0]['a']['port'] = '1';
        $coord = fake_manager::lab();
        $coord['nodes'][0]['x'] = 'left';
        return [
            'not json' => ['{'],
            'duplicate name' => [json_encode($dup)],
            'bad name' => [json_encode($name)],
            'link to a missing node' => [json_encode($link)],
            'port as a string' => [json_encode($port)],
            'coordinate as text' => [json_encode($coord)],
            'nodes as an object' => [json_encode(['nodes' => ['a' => 1]])],
        ];
    }

    /**
     * Definitions of the wrong shape are refused before anything is stored or sent.
     *
     * @param string $json broken definition
     */
    #[\PHPUnit\Framework\Attributes\DataProvider('broken_provider')]
    public function test_refuses_broken_definitions(string $json): void {
        $this->expectException(\moodle_exception::class);
        $this->expectExceptionMessage(get_string('labdefinvalid', 'gns3lab'));
        lab_definition::from_json($json, 'Lab');
    }

    public function test_title_and_names_fit_the_lab_manager(): void {
        $raw = fake_manager::lab();
        $raw['nodes'][0]['name'] = str_repeat('a', 80);
        $raw['links'] = [];

        $def = lab_definition::from_json(json_encode($raw), str_repeat('Я', 250));

        $this->assertSame(200, \core_text::strlen($def['title']));
        $this->assertSame(str_repeat('a', 80), $def['nodes'][0]['name']);
        $raw['nodes'][0]['name'] = str_repeat('a', 81);
        $this->expectException(\moodle_exception::class);
        lab_definition::from_json(json_encode($raw), 'Lab');
    }

    public function test_topology_for_the_viewer(): void {
        $def = lab_definition::from_json(json_encode(fake_manager::lab()), 'OSPF');

        $doc = lab_definition::to_topology($def, [json_decode(json_encode(fake_manager::image()))]);

        $r1 = $doc->topology->nodes[0];
        $this->assertSame('R1', $r1['node_id']);
        $this->assertSame('iou', $r1['node_type']);
        $this->assertSame(':/symbols/classic/router.svg', $r1['symbol']);
        $this->assertCount(8, $r1['ports']);
        $this->assertSame(
            ['adapter_number' => 0, 'port_number' => 1, 'name' => 'Ethernet0/1', 'short_name' => 'e0/1'],
            $r1['ports'][1]
        );
        $this->assertSame(
            [
                ['node_id' => 'R1', 'adapter_number' => 0, 'port_number' => 1],
                ['node_id' => 'R2', 'adapter_number' => 1, 'port_number' => 0],
            ],
            $doc->topology->links[0]['nodes']
        );
    }

    public function test_topology_without_the_image_catalog(): void {
        $def = lab_definition::from_json(json_encode(fake_manager::lab()), 'OSPF');

        $doc = lab_definition::to_topology($def, []);

        $this->assertSame('', $doc->topology->nodes[0]['symbol']);
        $this->assertSame('1/0', $doc->topology->nodes[0]['ports'][1]['name']);
    }
}
