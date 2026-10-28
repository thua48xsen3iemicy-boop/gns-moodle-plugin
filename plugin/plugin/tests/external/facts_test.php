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
 * Tests of mod_gns3lab_get_facts and mod_gns3lab_refresh_facts: who reads the facts of which lab.
 *
 * @package    mod_gns3lab
 * @category   test
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
#[\PHPUnit\Framework\Attributes\CoversClass(\mod_gns3lab\external\get_facts::class)]
#[\PHPUnit\Framework\Attributes\CoversClass(\mod_gns3lab\external\refresh_facts::class)]
#[\PHPUnit\Framework\Attributes\CoversClass(\mod_gns3lab\local\session_facts::class)]
final class facts_test extends \advanced_testcase {
    /** @var fake_manager */
    protected fake_manager $manager;

    /** @var \stdClass course */
    protected \stdClass $course;

    /** @var \stdClass activity with cmid */
    protected \stdClass $lab;

    /** @var \stdClass student */
    protected \stdClass $student;

    /** @var \stdClass non-editing teacher */
    protected \stdClass $teacher;

    protected function setUp(): void {
        parent::setUp();
        $this->resetAfterTest();
        $gen = $this->getDataGenerator();
        $this->course = $gen->create_course(['groupmode' => SEPARATEGROUPS, 'groupmodeforce' => 1]);
        $this->lab = $gen->create_module('gns3lab', ['course' => $this->course->id]);
        $this->student = $gen->create_and_enrol($this->course, 'student');
        $this->teacher = $gen->create_and_enrol($this->course, 'teacher');
        $this->manager = new fake_manager();
        $this->manager->install();
        $this->manager->on('GET', '/api/v1/sessions', 200, fake_manager::session());
        $this->manager->with_facts();
    }

    /**
     * Calls get_facts and cleans the result like the web service layer does.
     *
     * @param int $userid whose lab
     * @param int $sincerev rev the page has
     * @return array
     */
    protected function facts(int $userid = 0, int $sincerev = 0): array {
        return external_api::clean_returnvalue(
            get_facts::execute_returns(),
            get_facts::execute($this->lab->cmid, $userid, $sincerev)
        );
    }

    /**
     * Calls refresh_facts and cleans the result.
     *
     * @param int $userid whose lab
     * @return array
     */
    protected function refresh(int $userid = 0): array {
        return external_api::clean_returnvalue(
            refresh_facts::execute_returns(),
            refresh_facts::execute($this->lab->cmid, $userid)
        );
    }

    public function test_student_reads_the_facts_of_own_lab(): void {
        $this->setUser($this->student);

        $result = $this->facts();

        $this->assertTrue($result['available']);
        $this->assertTrue($result['changed']);
        $this->assertSame(5, $result['rev']);
        $this->assertSame(90, $result['ttl']);
        $this->assertGreaterThan(0, $result['generatedat']);
        $doc = json_decode($result['facts']);
        $this->assertSame('gns3lab.facts/1', $doc->schema);
        $this->assertSame('unsupported', $doc->nodes->{fake_manager::NODE_VNC}->error->code);
        // The session is found by the server for this user, never named by the browser.
        [$find] = $this->manager->requests_to('GET', '/api/v1/sessions');
        $this->assertSame("user_id={$this->student->id}&instance_id={$this->lab->id}", $find['query']);
    }

    public function test_l1_refreshes_timestamps_even_when_revision_has_not_changed(): void {
        $doc = fake_manager::facts();
        $doc['nodes'][fake_manager::NODE_TELNET]['l1_ports'] = [
            'ok' => true, 'ttl' => 15, 'observed_at' => gmdate('Y-m-d\TH:i:s\Z'),
            'ports' => [['port' => ['adapter' => 0, 'port' => 0], 'state' => 'up']],
        ];
        $this->manager->on('GET', fake_manager::STATE, 200, $doc);
        $this->setUser($this->student);
        $result = $this->facts(0, 5);
        $this->assertSame(5, $result['rev']);
        $this->assertTrue($result['changed']);
        $this->assertTrue(json_decode($result['facts'])->nodes->{fake_manager::NODE_TELNET}->l1_ports->ok);
    }

    public function test_same_rev_comes_back_without_the_document(): void {
        $this->setUser($this->student);

        $result = $this->facts(0, 5);

        $this->assertTrue($result['available']);
        $this->assertFalse($result['changed']);
        $this->assertSame(5, $result['rev']);
        $this->assertSame('', $result['facts']);
        $this->assertTrue($this->facts(0, 4)['changed']);
        $this->assertCount(1, $this->manager->requests_to('GET', fake_manager::STATE), 'second read from the cache');
    }

    public function test_no_facts_without_the_feature_or_a_session(): void {
        $session = fake_manager::session();
        $session['features'] = ['consoles'];
        $this->manager->on('GET', '/api/v1/sessions', 200, $session);
        $this->setUser($this->student);

        $this->assertSame(
            ['available' => false, 'changed' => false, 'rev' => 0, 'generatedat' => 0, 'ttl' => 0, 'facts' => ''],
            $this->facts()
        );

        $this->manager->on('GET', '/api/v1/sessions', 404, ['error' => 'session_not_found', 'message' => 'No session']);
        $this->assertFalse($this->facts()['available']);
        $this->assertSame([], $this->manager->requests_to('GET', fake_manager::STATE));
    }

    public function test_viewer_without_use_gets_nothing(): void {
        global $DB;
        $roleid = $DB->get_field('role', 'id', ['shortname' => 'student'], MUST_EXIST);
        assign_capability('mod/gns3lab:use', CAP_PROHIBIT, $roleid, \context_module::instance($this->lab->cmid));
        $this->setUser($this->student);

        try {
            $this->facts();
            $this->fail('No exception');
        } catch (\required_capability_exception $e) {
            $this->assertSame([], $this->manager->requests);
        }
        $this->expectException(\required_capability_exception::class);
        $this->refresh();
    }

    public function test_student_cannot_read_another_lab(): void {
        $other = $this->getDataGenerator()->create_and_enrol($this->course, 'student');
        $this->setUser($this->student);

        $this->expectException(\required_capability_exception::class);
        $this->facts((int) $other->id);
    }

    public function test_teacher_reads_a_student_of_own_group(): void {
        $group = $this->getDataGenerator()->create_group(['courseid' => $this->course->id]);
        $this->getDataGenerator()->create_group_member(['groupid' => $group->id, 'userid' => $this->student->id]);
        $this->getDataGenerator()->create_group_member(['groupid' => $group->id, 'userid' => $this->teacher->id]);
        $this->prohibit_access_all_groups();
        $this->setUser($this->teacher);

        $this->assertTrue($this->facts((int) $this->student->id)['available']);
        [$find] = $this->manager->requests_to('GET', '/api/v1/sessions');
        $this->assertSame("user_id={$this->student->id}&instance_id={$this->lab->id}", $find['query']);
    }

    public function test_teacher_outside_the_student_group_gets_nothing(): void {
        $group = $this->getDataGenerator()->create_group(['courseid' => $this->course->id]);
        $this->getDataGenerator()->create_group_member(['groupid' => $group->id, 'userid' => $this->student->id]);
        $this->prohibit_access_all_groups();
        $this->setUser($this->teacher);

        foreach ([fn() => $this->facts((int) $this->student->id), fn() => $this->refresh((int) $this->student->id)] as $call) {
            try {
                $call();
                $this->fail('No exception');
            } catch (\moodle_exception $e) {
                $this->assertSame('cannotobserveuser', $e->errorcode);
            }
        }
        $this->assertSame([], $this->manager->requests);
    }

    public function test_refresh_asks_the_manager_and_drops_the_cache(): void {
        $this->setUser($this->student);
        $this->facts();

        $this->assertSame(['requested' => true], $this->refresh());

        $this->assertCount(1, $this->manager->requests_to('POST', fake_manager::STATE . '/refresh'));
        $this->facts();
        $this->assertCount(2, $this->manager->requests_to('GET', fake_manager::STATE), 'cache dropped by the refresh');
    }

    public function test_refresh_only_for_a_running_session(): void {
        $this->manager->on('GET', '/api/v1/sessions', 200, fake_manager::session('stopped'));
        $this->setUser($this->student);

        try {
            $this->refresh();
            $this->fail('No exception');
        } catch (\moodle_exception $e) {
            $this->assertSame('sessionnotrunning', $e->errorcode);
        }
        $this->assertSame([], $this->manager->requests_to('POST', fake_manager::STATE . '/refresh'));
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
