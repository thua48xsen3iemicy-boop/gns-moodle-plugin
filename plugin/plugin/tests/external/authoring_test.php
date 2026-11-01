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
use mod_gns3lab\local\lab_definition;
use mod_gns3lab\local\lab_library;
use mod_gns3lab\tests\fake_manager;

/**
 * Author identity and system capability checks for derived images.
 *
 * @package mod_gns3lab
 * @category test
 * @copyright 2026 mod_gns3lab contributors
 * @license https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
#[\PHPUnit\Framework\Attributes\CoversClass(authoring::class)]
final class authoring_test extends \advanced_testcase {
    protected fake_manager $manager;
    protected \stdClass $author;
    protected int $labid;

    protected function setUp(): void {
        global $DB;
        parent::setUp();
        $this->resetAfterTest();
        $this->setAdminUser();
        $this->labid = lab_library::create_lab((object) ['name' => 'Authoring test']);
        $this->author = $this->getDataGenerator()->create_user();
        $role = $DB->get_field('role', 'id', ['shortname' => lab_library::AUTHOR_ROLE], MUST_EXIST);
        role_assign($role, $this->author->id, \context_system::instance());
        $this->setUser($this->author);
        $this->manager = new fake_manager();
        $this->manager->install();
        $this->manager->with_lab_definitions();
        $this->manager->with_info(['images', 'lab_definitions', 'image_capture', 'consoles']);
    }

    public function test_start_derives_author_identity_on_server(): void {
        $session = fake_manager::session('starting');
        $session['mode'] = 'authoring';
        $this->manager->on('POST', '/api/v1/sessions', 201, $session);
        $result = external_api::clean_returnvalue(authoring::execute_returns(),
            authoring::execute($this->labid, 'start', json_encode(fake_manager::lab())));
        $this->assertSame('starting', $result['session']['status']);
        [$request] = $this->manager->requests_to('POST', '/api/v1/sessions');
        $this->assertSame((int) $this->author->id, $request['body']['user_id']);
        $this->assertSame($this->labid, $request['body']['library_lab_id']);
        $this->assertArrayNotHasKey('instance_id', $request['body']);
        $this->assertSame('authoring', $request['body']['options']['mode']);
    }

    public function test_student_cannot_use_authoring_api(): void {
        $this->setUser($this->getDataGenerator()->create_user());
        $this->expectException(\required_capability_exception::class);
        authoring::execute($this->labid, 'state');
    }

    public function test_author_cannot_recommend_images_without_manageimages(): void {
        $image = fake_manager::host_image();
        $image['id'] = 'd-123456789abc';
        $image['derived'] = ['author_user_id' => $this->author->id];
        $this->manager->on('GET', '/api/v1/images', 200, ['images' => [$image]]);
        $this->expectException(\required_capability_exception::class);
        authoring::execute($this->labid, 'patch', imageid: $image['id'], featured: 1);
    }

    public function test_author_cannot_edit_another_authors_image(): void {
        $image = fake_manager::host_image();
        $image['id'] = 'd-123456789abc';
        $image['derived'] = ['author_user_id' => $this->getDataGenerator()->create_user()->id];
        $this->manager->on('GET', '/api/v1/images', 200, ['images' => [$image]]);
        $this->expectException(\required_capability_exception::class);
        authoring::execute($this->labid, 'patch', imageid: $image['id'], title: 'Not mine');
    }

    public function test_capture_application_refuses_a_concurrently_changed_draft(): void {
        $def = lab_definition::from_json(json_encode(fake_manager::lab()), 'Authoring test');
        lab_library::save_draft($this->labid, $def);
        $expected = lab_library::latest_version($this->labid)->definition;
        $changed = $def;
        $changed['nodes'][0]['x'] = 123;
        lab_library::save_draft($this->labid, $changed);
        try {
            lab_library::save_draft($this->labid, $def, $expected);
            $this->fail('A stale capture must not overwrite a changed draft');
        } catch (\moodle_exception $e) {
            $this->assertSame('authoringdraftconflict', $e->errorcode);
        }
        $this->assertSame(lab_definition::to_json($changed), lab_library::latest_version($this->labid)->definition);
    }

    /** Preserve the node-specific failure so the editor can offer retry or explicit force. */
    public function test_stopped_session_error_keeps_a_typed_blocking_node_list(): void {
        $session = fake_manager::session('stopped');
        $session['lab_def'] = 'sha256:aaaa';
        $this->manager->on('GET', '/api/v1/sessions', 200, $session);
        $this->manager->on('POST', '/api/v1/sessions/s-3f9a1c0d2b4e5f60/capture', 409, [
            'error' => 'guest_not_stopped', 'message' => 'Start the lab again',
            'nodes' => [['name' => 'PC1', 'reason' => 'SESSION_STOPPED']],
        ]);
        $result = external_api::clean_returnvalue(authoring::execute_returns(), authoring::execute(
            $this->labid, 'capture', json_encode(fake_manager::lab()), [['name' => 'PC1', 'title' => 'Prepared PC']]));
        $this->assertSame('GUEST_NOT_STOPPED', $result['capture']['errorcode']);
        $this->assertSame([['name' => 'PC1', 'reason' => 'SESSION_STOPPED']], $result['capture']['nodes']);
        $this->assertTrue($result['capture']['retryable']);
    }

    /** Force is an explicit, server-validated capture parameter. */
    public function test_explicit_force_is_forwarded_to_manager(): void {
        $session = fake_manager::session('running');
        $session['lab_def'] = 'sha256:aaaa';
        $this->manager->on('GET', '/api/v1/sessions', 200, $session);
        $this->manager->on('POST', '/api/v1/sessions/s-3f9a1c0d2b4e5f60/capture', 202, [
            'state' => 'running', 'stage' => 'quiesce', 'error' => null,
        ]);
        authoring::execute($this->labid, 'capture', json_encode(fake_manager::lab()),
            [['name' => 'PC1', 'title' => 'Prepared PC']], force: true);
        [$request] = $this->manager->requests_to('POST', '/api/v1/sessions/s-3f9a1c0d2b4e5f60/capture');
        $this->assertTrue($request['body']['force']);
    }
}
