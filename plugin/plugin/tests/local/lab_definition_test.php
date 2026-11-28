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
    public function test_keeps_reserved_adapter_zero_when_saving(): void {
        $raw = ['nodes' => [['name' => 'ISP1', 'image' => 'lab-uplink', 'x' => 0, 'y' => 0,
            'adapters' => 3, 'uplink_port' => 0]], 'links' => []];
        $def = lab_definition::from_json(json_encode($raw), 'ISP');
        $this->assertSame(0, $def['nodes'][0]['uplink_port']);
        $image = (object) ['id' => 'lab-uplink', 'kind' => 'qemu', 'uplink_port' => 0,
            'ports_per_adapter' => 1, 'port_name' => 'Ethernet{adapter}'];
        $topology = lab_definition::to_topology($def, [$image]);
        $ports = $topology->topology->nodes[0]['ports'];
        $this->assertSame([1, 2], array_column($ports, 'adapter_number'));
        // The lab page knows the provider of the lab by this mark (facts.js reach).
        $this->assertTrue($topology->topology->nodes[0]['uplink']);
        $plain = lab_definition::to_topology(lab_definition::from_json(json_encode(['nodes' => [['name' => 'PC1',
            'image' => 'debian12', 'x' => 0, 'y' => 0]], 'links' => []]), 'Plain'), []);
        $this->assertArrayNotHasKey('uplink', $plain->topology->nodes[0]);
    }

    public function test_keeps_random_interface_names_only_when_ticked(): void {
        $raw = ['nodes' => [['name' => 'PC1', 'image' => 'debian12', 'x' => 0, 'y' => 0, 'random_ifnames' => true],
            ['name' => 'PC2', 'image' => 'debian12', 'x' => 100, 'y' => 0, 'random_ifnames' => false]], 'links' => []];
        $def = lab_definition::from_json(json_encode($raw), 'Names');
        $this->assertTrue($def['nodes'][0]['random_ifnames']);
        $this->assertArrayNotHasKey('random_ifnames', $def['nodes'][1]);
        $raw['nodes'][1]['random_ifnames'] = 'yes';
        $this->expectException(\moodle_exception::class);
        lab_definition::from_json(json_encode($raw), 'Names');
    }

    public function test_no_paste_mark_stays_in_moodle(): void {
        $raw = ['nodes' => [['name' => 'R1', 'image' => 'cisco-iou-l3', 'x' => 0, 'y' => 0, 'nopaste' => true],
            ['name' => 'PC1', 'image' => 'debian12', 'x' => 100, 'y' => 0, 'nopaste' => false]], 'links' => []];
        $def = lab_definition::from_json(json_encode($raw), 'Paste');
        $this->assertTrue($def['nodes'][0]['nopaste']);
        $this->assertArrayNotHasKey('nopaste', $def['nodes'][1], 'only true is kept');

        $sent = lab_definition::for_manager($def);
        $this->assertArrayNotHasKey('nopaste', $sent['nodes'][0], 'the lab manager refuses unknown fields');
        $this->assertTrue($def['nodes'][0]['nopaste'], 'the stored definition keeps the mark');

        $images = [(object) ['id' => 'cisco-iou-l3', 'kind' => 'iou', 'console_type' => 'telnet', 'ports_per_adapter' => 4,
            'port_name' => 'Ethernet{adapter}/{port}']];
        $data = lab_definition::to_topology($def, $images);
        $this->assertTrue($data->topology->nodes[0]['nopaste']);
        $this->assertArrayNotHasKey('nopaste', $data->topology->nodes[1]);
        lab_definition::allow_paste($data);
        $this->assertArrayNotHasKey('nopaste', $data->topology->nodes[0], 'a teacher or an author may paste');

        $raw['nodes'][1]['nopaste'] = 1;
        $this->expectException(\moodle_exception::class);
        lab_definition::from_json(json_encode($raw), 'Paste');
    }

    public function test_closed_console_stays_in_moodle(): void {
        $raw = ['nodes' => [['name' => 'R1', 'image' => 'cisco-iou-l3', 'x' => 0, 'y' => 0, 'noconsole' => true],
            ['name' => 'WS1', 'image' => 'debian12', 'x' => 100, 'y' => 0, 'noconsole' => false]], 'links' => []];
        $def = lab_definition::from_json(json_encode($raw), 'SSH');
        $this->assertTrue($def['nodes'][0]['noconsole']);
        $this->assertArrayNotHasKey('noconsole', $def['nodes'][1], 'only true is kept');
        $this->assertSame(['R1'], lab_definition::closed_names($def));

        $sent = lab_definition::for_manager($def);
        $this->assertArrayNotHasKey('noconsole', $sent['nodes'][0], 'the lab manager refuses unknown fields');

        $data = lab_definition::to_topology($def, []);
        $this->assertTrue($data->topology->nodes[0]['noconsole']);
        $this->assertArrayNotHasKey('noconsole', $data->topology->nodes[1]);
        lab_definition::allow_paste($data);
        $this->assertArrayNotHasKey('noconsole', $data->topology->nodes[0], 'a teacher or an author keeps the console');

        $raw['nodes'][1]['noconsole'] = 'yes';
        $this->expectException(\moodle_exception::class);
        lab_definition::from_json(json_encode($raw), 'SSH');
    }

    public function test_extra_consoles_go_to_the_manager(): void {
        $raw = ['nodes' => [['name' => 'WS1', 'image' => 'debian12', 'x' => 0, 'y' => 0, 'extra_consoles' => 3],
            ['name' => 'WS2', 'image' => 'debian12', 'x' => 100, 'y' => 0, 'extra_consoles' => 0]], 'links' => []];
        $def = lab_definition::from_json(json_encode($raw), 'Lines');
        $this->assertSame(3, $def['nodes'][0]['extra_consoles']);
        $this->assertArrayNotHasKey('extra_consoles', $def['nodes'][1], '0 is no field, as at the manager');
        $this->assertSame(3, lab_definition::for_manager($def)['nodes'][0]['extra_consoles'], 'a field of the manager');

        $raw['nodes'][1]['extra_consoles'] = 4;
        $this->expectException(\moodle_exception::class);
        lab_definition::from_json(json_encode($raw), 'Lines');
    }

    public function test_icon_stays_in_moodle(): void {
        $raw = ['nodes' => [['name' => 'PC1', 'image' => 'debian12', 'x' => 0, 'y' => 0, 'icon' => 'router'],
            ['name' => 'PC2', 'image' => 'debian12', 'x' => 100, 'y' => 0, 'icon' => '']], 'links' => []];
        $def = lab_definition::from_json(json_encode($raw), 'Icons');
        $this->assertSame('router', $def['nodes'][0]['icon']);
        $this->assertArrayNotHasKey('icon', $def['nodes'][1], 'an empty icon is the icon of the image');

        $sent = lab_definition::for_manager($def);
        $this->assertArrayNotHasKey('icon', $sent['nodes'][0], 'the lab manager refuses unknown fields');

        $data = lab_definition::to_topology($def, []);
        $this->assertSame('router', $data->topology->nodes[0]['icon']);
        $this->assertArrayNotHasKey('icon', $data->topology->nodes[1]);

        $raw['nodes'][1]['icon'] = 'unknown';
        $this->expectException(\moodle_exception::class);
        lab_definition::from_json(json_encode($raw), 'Icons');
    }

    public function test_annotations_stay_in_moodle_and_become_drawings(): void {
        $raw = ['nodes' => [['name' => 'R1', 'image' => 'cisco-iou-l3', 'x' => 0, 'y' => 0]], 'links' => [],
            'annotations' => [
                ['kind' => 'box', 'x' => -20, 'y' => -40, 'w' => 300, 'h' => 200, 'text' => 'Office <A>', 'size' => 16,
                    'fill' => '#1E88E5', 'bold' => true, 'italic' => false, 'extra' => 1],
                ['kind' => 'text', 'x' => 10.4, 'y' => 200, 'text' => "line 1\r\nline \"2\"", 'color' => '#d32f2f',
                    'italic' => true],
                ['kind' => 'text', 'x' => 0, 'y' => 0, 'text' => "  \n "],
            ]];
        $def = lab_definition::from_json(json_encode($raw), 'Notes');
        $this->assertSame([
            ['kind' => 'box', 'x' => -20, 'y' => -40, 'w' => 300, 'h' => 200, 'text' => 'Office <A>', 'size' => 16,
                'fill' => '#1e88e5', 'bold' => true],
            ['kind' => 'text', 'x' => 10, 'y' => 200, 'text' => "line 1\nline \"2\"", 'size' => 14, 'color' => '#d32f2f',
                'italic' => true],
        ], $def['annotations'], 'known fields only, an empty text is dropped');

        $this->assertArrayNotHasKey('annotations', lab_definition::for_manager($def), 'the lab manager refuses unknown fields');

        $drawings = lab_definition::to_topology($def, [])->topology->drawings;
        $this->assertCount(2, $drawings);
        $this->assertSame([-20, -40, 0], [$drawings[0]['x'], $drawings[0]['y'], $drawings[0]['z']]);
        $this->assertStringContainsString('<rect x="0" y="0" width="300" height="200"', $drawings[0]['svg']);
        $this->assertStringContainsString('font-weight="bold" fill="#1e88e5">Office &lt;A&gt;</text>', $drawings[0]['svg']);
        $this->assertSame(1, $drawings[1]['z'], 'texts lie over the boxes');
        $this->assertSame(2, substr_count($drawings[1]['svg'], '<text '));
        $this->assertStringContainsString('font-style="italic" fill="#d32f2f">line &quot;2&quot;</text>', $drawings[1]['svg']);
        $this->assertNotFalse(simplexml_load_string($drawings[0]['svg']));

        $this->assertSame([], lab_definition::to_topology(lab_definition::from_json(json_encode(['nodes' => []]), 'x'), [])
            ->topology->drawings);
    }

    /**
     * Broken texts and boxes.
     *
     * @return array
     */
    public static function broken_annotations(): array {
        return [
            'not a list' => [['kind' => 'text', 'x' => 0, 'y' => 0, 'text' => 'a']],
            'kind' => [[['kind' => 'ellipse', 'x' => 0, 'y' => 0, 'text' => 'a']]],
            'no place' => [[['kind' => 'text', 'text' => 'a']]],
            'box without size' => [[['kind' => 'box', 'x' => 0, 'y' => 0, 'text' => 'a']]],
            'tiny box' => [[['kind' => 'box', 'x' => 0, 'y' => 0, 'w' => 5, 'h' => 50]]],
            'colour' => [[['kind' => 'text', 'x' => 0, 'y' => 0, 'text' => 'a', 'color' => 'red']]],
            'size' => [[['kind' => 'text', 'x' => 0, 'y' => 0, 'text' => 'a', 'size' => 200]]],
            'bold' => [[['kind' => 'text', 'x' => 0, 'y' => 0, 'text' => 'a', 'bold' => 1]]],
            'long text' => [[['kind' => 'text', 'x' => 0, 'y' => 0, 'text' => str_repeat('я', 501)]]],
        ];
    }

    /**
     * Refuses broken texts and boxes.
     *
     * @param array $annotations annotations
     */
    #[\PHPUnit\Framework\Attributes\DataProvider('broken_annotations')]
    public function test_refuses_broken_annotations(array $annotations): void {
        $this->expectException(\moodle_exception::class);
        lab_definition::from_json(json_encode(['nodes' => [], 'annotations' => $annotations]), 'Notes');
    }

    public function test_keeps_additional_disks_and_drops_an_empty_list(): void {
        $raw = ['nodes' => [['name' => 'PC1', 'image' => 'debian12', 'x' => 0, 'y' => 0,
            'disks' => [['size_gb' => 5, 'format' => 'raw'], ['size_gb' => 1]]],
            ['name' => 'PC2', 'image' => 'debian12', 'x' => 100, 'y' => 0, 'disks' => []]], 'links' => []];
        $def = lab_definition::from_json(json_encode($raw), 'Disks');
        $this->assertSame([['size_gb' => 5], ['size_gb' => 1]], $def['nodes'][0]['disks']);
        $this->assertArrayNotHasKey('disks', $def['nodes'][1]);
        $raw['nodes'][1]['disks'] = array_fill(0, 4, ['size_gb' => 1]);
        $this->expectException(\moodle_exception::class);
        lab_definition::from_json(json_encode($raw), 'Disks');
    }

    public function test_marks_mikrotik_nodes_for_their_console_snippets(): void {
        $raw = ['nodes' => [['name' => 'R1', 'image' => 'mikrotik-chr7', 'x' => 0, 'y' => 0],
            ['name' => 'PC1', 'image' => 'debian12', 'x' => 100, 'y' => 0]], 'links' => []];
        $def = lab_definition::from_json(json_encode($raw), 'CHR');
        $images = [
            (object) ['id' => 'mikrotik-chr7', 'kind' => 'qemu', 'guest' => 'routeros', 'ports_per_adapter' => 1,
                'port_name' => 'Ethernet{adapter}'],
            (object) ['id' => 'debian12', 'kind' => 'qemu', 'ports_per_adapter' => 1, 'port_name' => 'Ethernet{adapter}'],
        ];
        $nodes = lab_definition::to_topology($def, $images)->topology->nodes;
        $this->assertSame('routeros', $nodes[0]['guest']);
        $this->assertArrayNotHasKey('guest', $nodes[1]);
    }

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

    public function test_segments_make_a_lab_2_and_keep_their_known_fields(): void {
        $raw = fake_manager::lab_with_segments();
        $raw['segments'][0]['extra'] = 'dropped';
        $raw['segments'][0]['vlan']['random'] = [10, 20];
        $raw['segments'][0]['members'][0]['console'] = 1;
        $raw['values'] = 'shared';

        $def = lab_definition::from_json(json_encode($raw), 'LAN');

        $this->assertSame(lab_definition::FORMAT_2, $def['format']);
        $this->assertSame(['format', 'title', 'nodes', 'links', 'segments'], array_keys($def));
        $this->assertSame(
            [[
                'name' => 'LAN1',
                'vlan' => ['fixed' => 10],
                'subnet' => ['fixed' => '192.168.10.0/24'],
                'gateway' => ['node' => 'R1', 'adapter' => 0, 'port' => 0],
                'members' => [
                    ['node' => 'PC1', 'adapter' => 0, 'port' => 0],
                    ['node' => 'PC2', 'adapter' => 0, 'port' => 0, 'host' => 20],
                ],
            ]],
            $def['segments']
        );
    }

    public function test_a_lab_without_segments_is_lab_1_whatever_the_editor_says(): void {
        $raw = fake_manager::lab_with_segments();
        $raw['segments'] = [];

        $def = lab_definition::from_json(json_encode($raw), 'LAN');

        $this->assertSame(lab_definition::FORMAT, $def['format']);
        $this->assertArrayNotHasKey('segments', $def);
        $this->assertSame(lab_definition::FORMAT, lab_definition::empty('x')['format']);
    }

    /**
     * Segments the editor never sends.
     *
     * @return array
     */
    public static function broken_segments_provider(): array {
        $cases = [
            'name' => ['name', '1LAN'],
            'subnet' => ['subnet', ['fixed' => 24]],
            'vlan' => ['vlan', ['fixed' => '10']],
            'members' => ['members', 'PC1'],
            'member node' => ['members', [['node' => 'PC9', 'adapter' => 0, 'port' => 0]]],
            'member port' => ['members', [['node' => 'PC1', 'adapter' => '0', 'port' => 0]]],
            'member host' => ['members', [['node' => 'PC1', 'adapter' => 0, 'port' => 0, 'host' => 'x']]],
            'gateway node' => ['gateway', ['node' => 'R9', 'adapter' => 0, 'port' => 0]],
        ];
        $out = [];
        foreach ($cases as $title => [$key, $value]) {
            $raw = fake_manager::lab_with_segments();
            $raw['segments'][0][$key] = $value;
            $out[$title] = [json_encode($raw)];
        }
        $raw = fake_manager::lab_with_segments();
        $raw['segments'] = ['a' => 1];
        $out['segments as an object'] = [json_encode($raw)];
        return $out;
    }

    /**
     * Segments of the wrong shape are refused before anything is stored or sent.
     *
     * @param string $json broken definition
     */
    #[\PHPUnit\Framework\Attributes\DataProvider('broken_segments_provider')]
    public function test_refuses_broken_segments(string $json): void {
        $this->expectException(\moodle_exception::class);
        lab_definition::from_json($json, 'Lab');
    }

    public function test_segments_do_not_change_the_topology_for_the_viewer(): void {
        $def = lab_definition::from_json(json_encode(fake_manager::lab_with_segments()), 'LAN');

        $doc = lab_definition::to_topology($def, [json_decode(json_encode(fake_manager::image()))]);

        $this->assertCount(3, $doc->topology->nodes);
        $this->assertCount(1, $doc->topology->links);
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
