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
use mod_gns3lab\tests\fake_manager;

/**
 * Tests of mod_gns3lab_start_iso and mod_gns3lab_get_iso: only the owner mounts an ISO, only from a source node.
 *
 * @package    mod_gns3lab
 * @category   test
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
#[\PHPUnit\Framework\Attributes\CoversClass(\mod_gns3lab\external\start_iso::class)]
#[\PHPUnit\Framework\Attributes\CoversClass(\mod_gns3lab\external\get_iso::class)]
#[\PHPUnit\Framework\Attributes\CoversClass(\mod_gns3lab\local\session_data::class)]
final class iso_test extends \advanced_testcase {
    /** @var string path of the ISO route of the fake session */
    private const ISO = '/api/v1/sessions/s-3f9a1c0d2b4e5f60/iso';

    /** @var string an operation id */
    private const OP = '8c0e6a52-1d3f-4b7a-9e21-0a5c3d7f9b10';

    /** @var fake_manager */
    protected fake_manager $manager;

    /** @var \stdClass activity with cmid */
    protected \stdClass $lab;

    /** @var \stdClass student */
    protected \stdClass $student;

    protected function setUp(): void {
        parent::setUp();
        $this->resetAfterTest();
        $gen = $this->getDataGenerator();
        $course = $gen->create_course();
        $this->lab = $gen->create_module('gns3lab', ['course' => $course->id]);
        $this->student = $gen->create_and_enrol($course, 'student');
        $this->manager = new fake_manager();
        $this->manager->install();
        $this->manager->on('GET', '/api/v1/sessions', 200, self::session());
    }

    /**
     * A running session whose PC1 is a source and SW1 a target (manager CONTRACTS §2.12).
     *
     * @return array
     */
    private static function session(): array {
        $session = fake_manager::session();
        $session['features'][] = 'iso';
        $session['iso'] = ['sources' => [fake_manager::NODE_VNC], 'targets' => [fake_manager::NODE_TELNET, 'not-a-node']];
        return $session;
    }

    /**
     * An operation as the manager returns it.
     *
     * @param string $status running, succeeded or failed
     * @return array
     */
    private static function op(string $status): array {
        $op = ['schema' => 'gns3lab.iso/1', 'operation_id' => self::OP, 'status' => $status];
        if ($status === 'running') {
            $op += ['stage' => 'iso_pack', 'progress' => 0];
        } else if ($status === 'succeeded') {
            $op['result'] = ['files' => 2, 'bytes' => 4096,
                'targets' => [['node_id' => fake_manager::NODE_TELNET, 'status' => 'attached']]];
        } else {
            $op['error'] = ['code' => 'ISO_SOURCE_EMPTY', 'message' => 'No files'];
        }
        return $op;
    }

    /**
     * Calls start_iso as the web service layer does.
     *
     * @param string $nodeid source node
     * @return array
     */
    private function start(string $nodeid): array {
        return external_api::clean_returnvalue(
            start_iso::execute_returns(),
            start_iso::execute($this->lab->cmid, $nodeid, 'tab1')
        );
    }

    public function test_session_names_sources_and_targets_of_its_topology(): void {
        $this->setUser($this->student);
        $state = external_api::clean_returnvalue(get_state::execute_returns(), get_state::execute($this->lab->cmid));
        $this->assertSame([fake_manager::NODE_VNC], $state['session']['isosources']);
        $this->assertSame([fake_manager::NODE_TELNET], $state['session']['isotargets'], 'unknown node dropped');
    }

    public function test_owner_mounts_and_follows_the_operation(): void {
        $this->setUser($this->student);
        $this->manager->on('POST', self::ISO, 202, self::op('running'));
        $started = $this->start(fake_manager::NODE_VNC);
        $this->assertSame('running', $started['status']);
        $this->assertSame('iso_pack', $started['stage']);
        $request = $this->manager->requests_to('POST', self::ISO)[0];
        $this->assertSame(['node_id' => fake_manager::NODE_VNC, 'requested_by_user_id' => (int) $this->student->id],
            $request['body']);

        $this->manager->on('GET', self::ISO . '/' . self::OP, 200, self::op('succeeded'));
        $done = external_api::clean_returnvalue(get_iso::execute_returns(), get_iso::execute($this->lab->cmid, self::OP));
        $this->assertSame('succeeded', $done['status']);
        $this->assertSame(2, $done['files']);
        $this->assertSame([['nodeid' => fake_manager::NODE_TELNET, 'status' => 'attached', 'errorcode' => '']],
            $done['targets']);

        $this->manager->on('GET', self::ISO . '/' . self::OP, 200, self::op('failed'));
        $failed = external_api::clean_returnvalue(get_iso::execute_returns(), get_iso::execute($this->lab->cmid, self::OP));
        $this->assertSame('ISO_SOURCE_EMPTY', $failed['errorcode']);
    }

    public function test_only_a_source_node(): void {
        $this->setUser($this->student);
        $this->expectExceptionMessage(get_string('isonotavailable', 'gns3lab'));
        try {
            $this->start(fake_manager::NODE_TELNET);
        } finally {
            $this->assertSame([], $this->manager->requests_to('POST', self::ISO));
        }
    }

    public function test_manager_refusal_has_its_own_text(): void {
        $this->setUser($this->student);
        $this->manager->on('POST', self::ISO, 409, ['error' => 'iso_in_progress', 'message' => 'busy']);
        $this->expectExceptionMessage(get_string('isoinprogress', 'gns3lab'));
        $this->start(fake_manager::NODE_VNC);
    }

    public function test_no_iso_without_the_feature(): void {
        $this->setUser($this->student);
        $this->manager->on('GET', '/api/v1/sessions', 200, fake_manager::session());
        $this->expectExceptionMessage(get_string('isonotavailable', 'gns3lab'));
        $this->start(fake_manager::NODE_VNC);
    }
}
