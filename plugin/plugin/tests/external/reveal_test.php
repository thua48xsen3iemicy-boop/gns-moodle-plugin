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
use mod_gns3lab\local\reveal as store;
use mod_gns3lab\tests\fake_manager;

/**
 * Tests of the mask of the modes seen and fog: mod_gns3lab_reveal, mod_gns3lab_get_reveal and the refusal of a ticket
 * for a device a fog has not uncovered (docs/lab-constructor/README.md §2, §12.2).
 *
 * @package    mod_gns3lab
 * @category   test
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
#[\PHPUnit\Framework\Attributes\CoversClass(\mod_gns3lab\external\reveal::class)]
#[\PHPUnit\Framework\Attributes\CoversClass(\mod_gns3lab\external\get_reveal::class)]
#[\PHPUnit\Framework\Attributes\CoversClass(\mod_gns3lab\local\reveal::class)]
final class reveal_test extends \advanced_testcase {
    /** Ticket answer path of the running session. */
    private const TICKETS = '/api/v1/sessions/s-3f9a1c0d2b4e5f60/console-tickets';

    /** A cable of the live topology. */
    private const LINK = 'c1f3c2e0-0000-4000-8000-000000000001';

    /** @var fake_manager */
    protected fake_manager $manager;

    /** @var \stdClass course */
    protected \stdClass $course;

    /** @var \stdClass activity in fog with cmid */
    protected \stdClass $lab;

    /** @var \stdClass student */
    protected \stdClass $student;

    /** @var \stdClass another student */
    protected \stdClass $other;

    /** @var \stdClass non-editing teacher */
    protected \stdClass $teacher;

    protected function setUp(): void {
        parent::setUp();
        $this->resetAfterTest();
        $gen = $this->getDataGenerator();
        $this->course = $gen->create_course(['groupmode' => SEPARATEGROUPS, 'groupmodeforce' => 1]);
        // The fog starts from PC1 (the node with a screen); SW1 has to be found.
        $this->lab = $gen->create_module('gns3lab', [
            'course' => $this->course->id, 'factsview' => 'fog', 'fogstart' => ['PC1'],
        ]);
        $this->student = $gen->create_and_enrol($this->course, 'student');
        $this->other = $gen->create_and_enrol($this->course, 'student');
        $this->teacher = $gen->create_and_enrol($this->course, 'teacher');

        $this->manager = new fake_manager();
        $this->manager->install();
        $this->session();
        $this->manager->on(
            'POST',
            self::TICKETS,
            200,
            ['ticket' => 't-1', 'gateway_url' => 'wss://gw.example.org', 'expires_in' => 30]
        );
    }

    /**
     * The running session of the manager, with a cable between the two nodes.
     *
     * @param string $status session status
     */
    protected function session(string $status = 'running'): void {
        $session = fake_manager::session($status);
        if ($status === 'running') {
            $session['topology']['topology']['links'] = [[
                'link_id' => self::LINK,
                'nodes' => [
                    ['node_id' => fake_manager::NODE_TELNET, 'adapter_number' => 0, 'port_number' => 0],
                    ['node_id' => fake_manager::NODE_VNC, 'adapter_number' => 0, 'port_number' => 0],
                ],
            ]];
        }
        $this->manager->on('GET', '/api/v1/sessions', 200, $session);
    }

    /**
     * An item of a call.
     *
     * @param string $kind node, known, iface, group or link
     * @param string $nodeid node id, empty for a link
     * @param string $item interface name, group or link id
     * @return array
     */
    protected function item(string $kind, string $nodeid = '', string $item = ''): array {
        return ['nodeid' => $nodeid, 'kind' => $kind, 'item' => $item];
    }

    /**
     * Adds items the way the web service layer does.
     *
     * @param array[] $items items
     * @return array
     */
    protected function add(array $items): array {
        $result = reveal::execute($this->lab->cmid, $items);
        return external_api::clean_returnvalue(reveal::execute_returns(), $result);
    }

    /**
     * Reads the mask the way the web service layer does.
     *
     * @param int $userid whose lab, 0 for the current user
     * @return array[] items sorted for comparison
     */
    protected function read(int $userid = 0): array {
        $result = get_reveal::execute($this->lab->cmid, $userid);
        $items = external_api::clean_returnvalue(get_reveal::execute_returns(), $result)['items'];
        usort($items, fn($a, $b) => [$a['kind'], $a['item']] <=> [$b['kind'], $b['item']]);
        return $items;
    }

    /**
     * The ticket of the current user.
     *
     * @param string $nodeid node id
     * @param int $userid whose lab
     * @return array
     */
    protected function ticket(string $nodeid, int $userid = 0): array {
        $result = get_console_ticket::execute($this->lab->cmid, $nodeid, $userid);
        return external_api::clean_returnvalue(get_console_ticket::execute_returns(), $result);
    }

    public function test_a_student_adds_items_and_reads_them_back(): void {
        $this->setUser($this->student);

        $result = $this->add([
            $this->item('node', fake_manager::NODE_VNC),
            $this->item('known', fake_manager::NODE_TELNET),
            $this->item('iface', fake_manager::NODE_VNC, 'ens3'),
            $this->item('group', fake_manager::NODE_VNC, 'l3'),
            $this->item('link', '', self::LINK),
        ]);

        $this->assertSame(5, $result['added']);
        $this->assertSame([
            $this->item('group', fake_manager::NODE_VNC, 'l3'),
            $this->item('iface', fake_manager::NODE_VNC, 'ens3'),
            $this->item('known', fake_manager::NODE_TELNET),
            $this->item('link', '', self::LINK),
            $this->item('node', fake_manager::NODE_VNC),
        ], $this->read());
    }

    public function test_items_are_only_added_and_only_once(): void {
        $this->setUser($this->student);
        $this->add([$this->item('node', fake_manager::NODE_VNC), $this->item('node', fake_manager::NODE_VNC)]);

        $again = $this->add([$this->item('node', fake_manager::NODE_VNC), $this->item('group', fake_manager::NODE_VNC, 'l1')]);

        $this->assertSame(1, $again['added']);
        $this->assertCount(2, $this->read());
        // Nothing in the interface takes an item away: an empty call leaves the mask as it is.
        $this->assertSame(0, $this->add([])['added']);
        $this->assertCount(2, $this->read());
    }

    public function test_every_student_has_a_mask_of_their_own(): void {
        $this->setUser($this->student);
        $this->add([$this->item('node', fake_manager::NODE_VNC)]);

        $this->setUser($this->other);

        $this->assertSame([], $this->read());
        $this->assertSame(1, $this->add([$this->item('node', fake_manager::NODE_TELNET)])['added']);
        $this->setUser($this->student);
        $this->assertSame([$this->item('node', fake_manager::NODE_VNC)], $this->read());
    }

    public function test_a_student_cannot_read_the_mask_of_another(): void {
        $this->setUser($this->student);

        $this->expectException(\required_capability_exception::class);
        $this->read((int) $this->other->id);
    }

    public function test_a_teacher_of_the_group_reads_the_mask_of_a_student(): void {
        $group = $this->getDataGenerator()->create_group(['courseid' => $this->course->id]);
        $this->getDataGenerator()->create_group_member(['groupid' => $group->id, 'userid' => $this->student->id]);
        $this->getDataGenerator()->create_group_member(['groupid' => $group->id, 'userid' => $this->teacher->id]);
        $this->prohibit_access_all_groups();
        $this->setUser($this->student);
        $this->add([$this->item('node', fake_manager::NODE_VNC)]);

        $this->setUser($this->teacher);

        $this->assertSame([$this->item('node', fake_manager::NODE_VNC)], $this->read((int) $this->student->id));
        // The teacher's own mask is another one: writing has no user to name.
        $this->assertSame([], $this->read());
    }

    public function test_a_teacher_outside_the_group_does_not_read_the_mask(): void {
        $group = $this->getDataGenerator()->create_group(['courseid' => $this->course->id]);
        $this->getDataGenerator()->create_group_member(['groupid' => $group->id, 'userid' => $this->student->id]);
        $this->prohibit_access_all_groups();
        $this->setUser($this->teacher);

        try {
            $this->read((int) $this->student->id);
            $this->fail('No exception');
        } catch (\moodle_exception $e) {
            $this->assertSame('cannotobserveuser', $e->errorcode);
        }
    }

    public function test_only_a_running_session_takes_items(): void {
        global $DB;
        $this->session('starting');
        $this->setUser($this->student);

        try {
            $this->add([$this->item('node', fake_manager::NODE_VNC)]);
            $this->fail('No exception');
        } catch (\moodle_exception $e) {
            $this->assertSame('sessionnotrunning', $e->errorcode);
        }
        $this->assertSame(0, $DB->count_records('gns3lab_reveal'));
    }

    public function test_a_user_who_cannot_use_the_activity_cannot_write(): void {
        $guest = $this->getDataGenerator()->create_user();
        $this->setUser($guest);

        $this->expectException(\moodle_exception::class);
        $this->add([$this->item('node', fake_manager::NODE_VNC)]);
    }

    #[\PHPUnit\Framework\Attributes\DataProvider('bad_items')]
    public function test_what_is_not_in_the_topology_or_not_well_formed_is_refused(array $item): void {
        global $DB;
        $this->setUser($this->student);

        try {
            $this->add([$this->item('node', fake_manager::NODE_VNC), $item]);
            $this->fail('No exception');
        } catch (\invalid_parameter_exception $e) {
            // Refused.
            $this->assertInstanceOf(\invalid_parameter_exception::class, $e);
        }
        // The call is refused as a whole.
        $this->assertSame(0, $DB->count_records('gns3lab_reveal'));
    }

    /**
     * Items that are refused.
     *
     * @return array
     */
    public static function bad_items(): array {
        $node = fake_manager::NODE_VNC;
        return [
            'unknown node' => [['nodeid' => 'gone', 'kind' => 'node', 'item' => '']],
            'unknown link' => [['nodeid' => '', 'kind' => 'link', 'item' => 'nope']],
            'a link names a node' => [['nodeid' => $node, 'kind' => 'link', 'item' => self::LINK]],
            'unknown kind' => [['nodeid' => $node, 'kind' => 'what', 'item' => '']],
            'a node with an item' => [['nodeid' => $node, 'kind' => 'node', 'item' => 'x']],
            'unknown group' => [['nodeid' => $node, 'kind' => 'group', 'item' => 'l4']],
            'an interface without a name' => [['nodeid' => $node, 'kind' => 'iface', 'item' => '']],
            'a name with a space' => [['nodeid' => $node, 'kind' => 'iface', 'item' => 'bad name']],
            'a name that is too long' => [['nodeid' => $node, 'kind' => 'iface', 'item' => str_repeat('a', 101)]],
        ];
    }

    public function test_a_call_takes_500_items_at_most(): void {
        $this->setUser($this->student);
        $items = [];
        for ($i = 0; $i <= store::MAX_PER_CALL; $i++) {
            $items[] = $this->item('iface', fake_manager::NODE_VNC, 'if' . $i);
        }

        $this->expectException(\invalid_parameter_exception::class);
        $this->add($items);
    }

    public function test_a_user_has_5000_rows_in_an_activity_at_most(): void {
        global $DB;
        $rows = [];
        for ($i = 0; $i < store::MAX_PER_USER; $i++) {
            $rows[] = (object) [
                'gns3labid' => $this->lab->id, 'userid' => $this->student->id, 'nodeid' => fake_manager::NODE_VNC,
                'kind' => 'iface', 'item' => 'if' . $i, 'timecreated' => time(),
            ];
        }
        $DB->insert_records('gns3lab_reveal', $rows);
        $this->setUser($this->student);

        try {
            $this->add([$this->item('iface', fake_manager::NODE_VNC, 'one-more')]);
            $this->fail('No exception');
        } catch (\moodle_exception $e) {
            $this->assertSame('revealfull', $e->errorcode);
        }
        // What is there already is not a new item.
        $this->assertSame(0, $this->add([$this->item('iface', fake_manager::NODE_VNC, 'if0')])['added']);
        // The mask of another student has its own room.
        $this->setUser($this->other);
        $this->assertSame(1, $this->add([$this->item('iface', fake_manager::NODE_VNC, 'one-more')])['added']);
    }

    public function test_the_rows_of_an_earlier_project_are_dropped_when_the_mask_is_written(): void {
        global $DB;
        $old = fn(string $kind, string $nodeid, string $item) => (object) [
            'gns3labid' => $this->lab->id, 'userid' => $this->student->id, 'nodeid' => $nodeid, 'kind' => $kind,
            'item' => $item, 'timecreated' => time(),
        ];
        $DB->insert_records('gns3lab_reveal', [
            $old('node', 'node-of-the-old-project', ''),
            $old('iface', 'node-of-the-old-project', 'ens3'),
            $old('link', '', 'link-of-the-old-project'),
            $old('node', fake_manager::NODE_TELNET, ''),
        ]);
        $this->setUser($this->student);

        $this->add([$this->item('node', fake_manager::NODE_VNC)]);

        $this->assertSame([
            $this->item('node', fake_manager::NODE_TELNET),
            $this->item('node', fake_manager::NODE_VNC),
        ], array_values(array_filter($this->read(), fn($i) => $i['kind'] === 'node')));
        $this->assertSame(2, $DB->count_records('gns3lab_reveal'));
    }

    public function test_a_node_that_is_not_found_gets_no_ticket_in_a_fog_with_starting_nodes(): void {
        $this->setUser($this->student);

        // PC1 is the starting node.
        $this->assertSame('t-1', $this->ticket(fake_manager::NODE_VNC)['ticket']);
        // SW1 is not found yet: the manager is not even asked.
        $this->manager->requests = [];
        try {
            $this->ticket(fake_manager::NODE_TELNET);
            $this->fail('No exception');
        } catch (\moodle_exception $e) {
            $this->assertSame('nodenotfound', $e->errorcode);
        }
        $this->assertSame([], $this->manager->requests_to('POST', self::TICKETS));

        // A cable that works found it.
        $this->add([$this->item('known', fake_manager::NODE_TELNET)]);
        $this->assertSame('t-1', $this->ticket(fake_manager::NODE_TELNET)['ticket']);
    }

    public function test_a_node_that_was_opened_has_a_ticket_too(): void {
        $this->setUser($this->student);
        $this->add([$this->item('node', fake_manager::NODE_TELNET)]);

        $this->assertSame('t-1', $this->ticket(fake_manager::NODE_TELNET)['ticket']);
    }

    public function test_the_mask_of_another_student_does_not_open_a_node(): void {
        $this->setUser($this->other);
        $this->add([$this->item('known', fake_manager::NODE_TELNET)]);

        $this->setUser($this->student);
        $this->expectException(\moodle_exception::class);
        $this->ticket(fake_manager::NODE_TELNET);
    }

    public function test_a_teacher_sees_everything_and_gets_a_ticket_for_a_node_not_found(): void {
        $group = $this->getDataGenerator()->create_group(['courseid' => $this->course->id]);
        $this->getDataGenerator()->create_group_member(['groupid' => $group->id, 'userid' => $this->student->id]);
        $this->getDataGenerator()->create_group_member(['groupid' => $group->id, 'userid' => $this->teacher->id]);
        $this->prohibit_access_all_groups();
        $this->setUser($this->teacher);

        $ticket = $this->ticket(fake_manager::NODE_TELNET, (int) $this->student->id);

        $this->assertTrue($ticket['readonly']);
    }

    public function test_a_fog_without_starting_nodes_opens_every_console(): void {
        global $DB;
        $DB->set_field('gns3lab', 'fogstart', '', ['id' => $this->lab->id]);
        $this->setUser($this->student);

        $this->assertSame('t-1', $this->ticket(fake_manager::NODE_TELNET)['ticket']);
    }

    public function test_starting_names_that_match_no_node_are_ignored(): void {
        global $DB;
        $DB->set_field('gns3lab', 'fogstart', json_encode(['nobody']), ['id' => $this->lab->id]);
        $this->setUser($this->student);

        // The lab was changed after the setting was made: nobody could open anything otherwise.
        $this->assertSame('t-1', $this->ticket(fake_manager::NODE_TELNET)['ticket']);
    }

    public function test_the_other_modes_do_not_close_a_console(): void {
        global $DB;
        foreach (['live', 'seen'] as $mode) {
            $DB->set_field('gns3lab', 'factsview', $mode, ['id' => $this->lab->id]);
            $this->setUser($this->student);

            $this->assertSame('t-1', $this->ticket(fake_manager::NODE_TELNET)['ticket'], $mode);
        }
    }

    /**
     * Takes moodle/site:accessallgroups from non-editing teachers in the activity.
     */
    protected function prohibit_access_all_groups(): void {
        global $DB;
        $roleid = $DB->get_field('role', 'id', ['shortname' => 'teacher'], MUST_EXIST);
        assign_capability('moodle/site:accessallgroups', CAP_PROHIBIT, $roleid, \context_module::instance($this->lab->cmid));
    }
}
