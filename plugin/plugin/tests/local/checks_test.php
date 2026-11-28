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

/**
 * Tests of the auto-check settings: overrides, thresholds, grading for the manager and the gradebook value of a mark.
 *
 * @package    mod_gns3lab
 * @category   test
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
#[\PHPUnit\Framework\Attributes\CoversClass(\mod_gns3lab\local\checks::class)]
final class checks_test extends \basic_testcase {
    public function test_overrides_take_known_codes_and_classes_only(): void {
        [$out, $bad] = checks::parse_overrides("hostname_wrong = note\n\nvlan_extra=minor\nbogus=note\nlink_down=fatal\nnoequals");
        $this->assertSame(['hostname_wrong' => 'note', 'vlan_extra' => 'minor'], $out);
        $this->assertSame(['bogus=note', 'link_down=fatal', 'noequals'], $bad);
        $this->assertSame("hostname_wrong=note\nvlan_extra=minor", checks::format_overrides($out));
    }

    public function test_the_scripts_of_the_teacher_take_an_override(): void {
        [$out, $bad] = checks::parse_overrides("script_failed=minor\nfacts_conflict=major");
        $this->assertSame(['script_failed' => 'minor'], $out);
        $this->assertSame(['facts_conflict=major'], $bad);
        $instance = (object) ['checksoverrides' => json_encode(['script_failed' => 'note', 'bogus' => 'note'])];
        $this->assertSame(['script_failed' => 'note'], checks::overrides($instance));
    }

    public function test_thresholds_follow_the_rules_of_the_manager(): void {
        $rows = checks::clean_thresholds([
            ['mark' => '5', 'critical' => '0', 'major' => '0', 'minor' => '1'],
            ['mark' => '', 'critical' => '', 'major' => '', 'minor' => ''],
            ['mark' => 4, 'critical' => '', 'major' => 2, 'minor' => null],
        ]);
        $this->assertSame([
            ['mark' => 5, 'critical' => 0, 'major' => 0, 'minor' => 1],
            ['mark' => 4, 'critical' => null, 'major' => 2, 'minor' => null],
        ], $rows);
        $this->assertSame('', checks::thresholds_error($rows, 2));
        $this->assertSame('checksfailmarkhigh', checks::thresholds_error($rows, 4));
        $this->assertSame('checksthresholdsorder', checks::thresholds_error(array_reverse($rows), 2));
        $this->assertSame('checksthresholdsnone', checks::thresholds_error([], 2));
        $this->assertSame(
            'checksthresholdsmark',
            checks::thresholds_error(checks::clean_thresholds([['mark' => 'x']]), 2)
        );
        $this->assertSame(
            'checksthresholdslimit',
            checks::thresholds_error(checks::clean_thresholds([['mark' => 5, 'minor' => '-1']]), 2)
        );
    }

    public function test_grading_of_an_activity(): void {
        $instance = (object) ['checksprofile' => 'bogus', 'checksthresholds' => 'not json', 'checksfailmark' => 2,
            'checksoverrides' => json_encode(['hostname_wrong' => 'note', 'nope' => 'minor'])];
        $grading = checks::grading($instance);
        $this->assertSame('standard', $grading['profile']);
        $this->assertSame(checks::DEFAULT_THRESHOLDS, $grading['thresholds']);
        $this->assertSame(['hostname_wrong' => 'note'], $grading['overrides']);
        // No overrides go as a JSON object, not a list.
        $this->assertSame('{}', json_encode(checks::grading((object) [])['overrides']));
    }

    public function test_report_visibility(): void {
        $now = 1000;
        $this->assertTrue(checks::details_visible((object) ['checksreport' => 'full'], false, $now));
        $this->assertFalse(checks::details_visible((object) ['checksreport' => 'counts'], false, $now));
        $this->assertTrue(checks::details_visible((object) ['checksreport' => 'counts'], true, $now));
        $after = (object) ['checksreport' => 'after', 'checksreportdate' => 2000];
        $this->assertFalse(checks::details_visible($after, false, $now));
        $this->assertTrue(checks::details_visible($after, false, 2000));
    }

    public function test_grade_value_for_points_and_scales(): void {
        $this->assertEqualsWithDelta(80.0, checks::grade_value(4, 5, 2, 100), 0.0001);
        $this->assertEqualsWithDelta(4.0, checks::grade_value(2, 5, 2, 10), 0.0001);
        $this->assertEqualsWithDelta(5.0, checks::grade_value(5, 5, 2, 5), 0.0001);
        // A scale of four items: one item per mark from 2 to 5.
        $this->assertSame(1.0, checks::grade_value(2, 5, 2, -3, 4));
        $this->assertSame(3.0, checks::grade_value(4, 5, 2, -3, 4));
        $this->assertSame(4.0, checks::grade_value(5, 5, 2, -3, 4));
        // A scale of two items (fail, pass): 3 and above pass.
        $this->assertSame(2.0, checks::grade_value(4, 5, 2, -3, 2));
        $this->assertSame(1.0, checks::grade_value(2, 5, 2, -3, 2));
        $this->assertNull(checks::grade_value(4, 5, 2, 0));
    }

    public function test_attempts_left(): void {
        $this->assertSame(-1, checks::attempts_left((object) ['checksattempts' => 0], 7));
        $this->assertSame(1, checks::attempts_left((object) ['checksattempts' => 3], 2));
        $this->assertSame(0, checks::attempts_left((object) ['checksattempts' => 3], 5));
    }

    public function test_the_checks_of_an_activity_are_a_checks_document_or_nothing(): void {
        $this->assertNull(checks::parse_document(''));
        $this->assertNull(checks::parse_document('[1]'));
        $this->assertNull(checks::parse_document('{"format":"gns3lab.lab/1"}'));
        $this->assertNull(checks::parse_document('{"format":"gns3lab.checks/1","x":"' . str_repeat('a', 70000) . '"}'));
        $doc = checks::parse_document('{"format":"gns3lab.checks/1","rules":{},"scripts":[{"id":"a","expect":{}}]}');
        // Empty objects stay objects for the lab manager, and the document goes back as it came.
        $this->assertInstanceOf(\stdClass::class, $doc['rules']);
        $this->assertInstanceOf(\stdClass::class, $doc['scripts'][0]['expect']);
        $this->assertSame(
            '{"format":"gns3lab.checks/1","rules":{},"scripts":[{"id":"a","expect":{}}]}',
            checks::encode_document($doc)
        );
        $this->assertSame('', checks::encode_document(null));
        $this->assertNull(checks::activity_document((object) ['checksdef' => null]));
    }

    public function test_only_another_known_description_is_earlier_checks(): void {
        $own = (object) ['checksdefhash' => 'sha256:new'];
        $this->assertTrue(checks::outdated($own, 'sha256:old'));
        $this->assertFalse(checks::outdated($own, 'sha256:new'));
        $this->assertFalse(checks::outdated($own, null)); // An unknown description is never called old
        $this->assertFalse(checks::outdated((object) ['checksdefhash' => null], 'sha256:old')); // The checks of the lab
    }

    public function test_the_checks_of_a_lab_and_a_lab_without_them(): void {
        $lab = ['format' => lab_definition::FORMAT_2, 'nodes' => [], 'links' => [], 'segments' => [],
            'checks' => ['format' => 'gns3lab.checks/1', 'network' => 'off']];
        $this->assertSame(['format' => 'gns3lab.checks/1', 'network' => 'off'], checks::lab_document($lab));
        $this->assertNull(checks::lab_document('cisco-3x2')); // A reference lab keeps its own on the manager
        $bare = lab_definition::without_checks($lab);
        $this->assertSame(lab_definition::FORMAT, $bare['format']); // Nothing but the checks made it lab/2
        $this->assertArrayNotHasKey('segments', $bare);
        $segmented = lab_definition::without_checks(['segments' => [['name' => 'A']]] + $lab);
        $this->assertSame(lab_definition::FORMAT_2, $segmented['format']);
        $this->assertArrayNotHasKey('checks', $segmented);
    }
}
