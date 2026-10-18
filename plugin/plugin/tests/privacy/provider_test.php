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

namespace mod_gns3lab\privacy;

use core_privacy\local\request\approved_contextlist;
use core_privacy\local\request\approved_userlist;
use core_privacy\local\request\userlist;
use core_privacy\local\request\writer;
use mod_gns3lab\local\lab_library;

/**
 * Tests of the privacy provider: authorship of library labs.
 *
 * @package    mod_gns3lab
 * @category   test
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
#[\PHPUnit\Framework\Attributes\CoversClass(\mod_gns3lab\privacy\provider::class)]
final class provider_test extends \core_privacy\tests\provider_testcase {
    public function test_lab_authors(): void {
        global $DB;
        $this->resetAfterTest();
        $author = $this->getDataGenerator()->create_user();
        $other = $this->getDataGenerator()->create_user();
        $this->setUser($author);
        $labid = lab_library::create_lab((object) ['name' => 'OSPF']);
        $system = \context_system::instance();

        $contextids = array_map('intval', provider::get_contexts_for_userid($author->id)->get_contextids());
        $this->assertSame([(int) $system->id], $contextids);
        $this->assertSame([], provider::get_contexts_for_userid($other->id)->get_contextids());
        $userlist = new userlist($system, 'mod_gns3lab');
        provider::get_users_in_context($userlist);
        $this->assertSame([(int) $author->id], array_map('intval', $userlist->get_userids()));

        provider::export_user_data(new approved_contextlist($author, 'mod_gns3lab', [$system->id]));
        $data = writer::with_context($system)->get_data([get_string('library', 'gns3lab')]);
        $this->assertSame('OSPF', $data->labs[0]['name']);

        // The lab stays in the library, the administrator becomes its author.
        provider::delete_data_for_users(new approved_userlist($system, 'mod_gns3lab', [$author->id]));
        $lab = $DB->get_record('gns3lab_labs', ['id' => $labid]);
        $this->assertEquals(get_admin()->id, $lab->usercreated);
        $this->assertEquals(get_admin()->id, lab_library::latest_version($labid)->usermodified);
        $this->assertSame([], provider::get_contexts_for_userid($author->id)->get_contextids());
    }
}
