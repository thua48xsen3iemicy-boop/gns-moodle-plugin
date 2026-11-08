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
 * Auto-check settings of an activity (docs/autochecks.md): the rule catalog, the grading sent to the lab manager, who
 * sees the detailed report and how a mark of the manager becomes a grade of the gradebook.
 *
 * The lab manager grades (CONTRACTS §2.9, docs/autochecks/README.md §5); the plugin only passes the settings of the
 * activity and keeps the result. The rule codes below are the catalog of the manager (`lab_manager/checks.py`, RULES):
 * an override of an unknown code is refused by the manager with 400 invalid_request, so the form checks them first.
 *
 * @package    mod_gns3lab
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
class checks {
    /** Strictness profiles of the manager; the first one is not the default. */
    public const PROFILES = ['lenient', 'standard', 'strict'];

    /** Default profile. */
    public const DEFAULT_PROFILE = 'standard';

    /** Classes of a finding, from the most severe. */
    public const CLASSES = ['critical', 'major', 'minor', 'note'];

    /** The detailed report is always shown to the student. */
    public const REPORT_FULL = 'full';

    /** The student sees the mark and the counts only. */
    public const REPORT_COUNTS = 'counts';

    /** The student sees the mark and the counts, the findings after a date set in the activity. */
    public const REPORT_AFTER = 'after';

    /** Report modes. */
    public const REPORTS = [self::REPORT_FULL, self::REPORT_COUNTS, self::REPORT_AFTER];

    /** Rows of thresholds in the activity form; the manager takes up to 10. */
    public const THRESHOLD_ROWS = 4;

    /** Default thresholds (docs/autochecks/README.md §5.3): mark, then the most critical, major and minor findings. */
    public const DEFAULT_THRESHOLDS = [
        ['mark' => 5, 'critical' => 0, 'major' => 0, 'minor' => 1],
        ['mark' => 4, 'critical' => 0, 'major' => 1, 'minor' => 3],
        ['mark' => 3, 'critical' => 0, 'major' => 3, 'minor' => null],
    ];

    /** Default mark when no row of the thresholds fits. */
    public const DEFAULT_FAIL_MARK = 2;

    /** Highest mark the manager takes. */
    public const MAX_MARK = 100;

    /**
     * Rule catalog of the lab manager: code => group (CONTRACTS §2.9, docs/autochecks/README.md §8).
     *
     * Keep in step with RULES of manager/lab_manager/checks.py; the services codes (facts_conflict, script_failed)
     * are not in it and take no override.
     */
    public const RULES = [
        'node_stopped' => 'l1', 'config_not_saved' => 'l1', 'link_suspended' => 'l1', 'port_shutdown' => 'l1',
        'link_down' => 'l1', 'unused_port_up' => 'l1',
        'segments_merged' => 'l2', 'segment_split' => 'l2', 'member_isolated' => 'l2', 'vlan_mismatch' => 'l2',
        'tag_not_allowed' => 'l2', 'vlan_wrong' => 'l2', 'vlan_name_wrong' => 'l2', 'vlan_extra' => 'l2',
        'vlan_extra_used' => 'l2', 'trunk_not_pruned' => 'l2', 'mgmt_vlan_missing' => 'l2',
        'address_missing' => 'addressing', 'address_wrong' => 'addressing', 'mask_wrong' => 'addressing',
        'address_misplaced' => 'addressing', 'address_extra' => 'addressing', 'address_apipa' => 'addressing',
        'address_not_private' => 'addressing', 'address_duplicate' => 'addressing', 'subnet_overlap' => 'addressing',
        'subnet_mixed' => 'addressing', 'address_not_saved' => 'addressing', 'config_not_applied' => 'addressing',
        'gateway_missing' => 'gateways', 'gateway_wrong' => 'gateways', 'unreachable' => 'routing',
        'hostname_wrong' => 'host', 'forwarding_off' => 'host', 'forwarding_on_host' => 'host',
    ];

    /**
     * Whether the activity has auto-checks switched on (whatever the lab manager offers).
     *
     * @param \stdClass $instance gns3lab record
     * @return bool
     */
    public static function enabled(\stdClass $instance): bool {
        return !empty($instance->checksenabled);
    }

    /**
     * The strictness profile of an activity.
     *
     * @param \stdClass $instance gns3lab record
     * @return string
     */
    public static function profile(\stdClass $instance): string {
        $profile = (string) ($instance->checksprofile ?? '');
        return in_array($profile, self::PROFILES, true) ? $profile : self::DEFAULT_PROFILE;
    }

    /**
     * The thresholds of an activity; a missing or broken value gives the defaults.
     *
     * @param \stdClass $instance gns3lab record
     * @return array rows ['mark' => int, 'critical' => int|null, 'major' => int|null, 'minor' => int|null]
     */
    public static function thresholds(\stdClass $instance): array {
        $raw = json_decode((string) ($instance->checksthresholds ?? ''), true);
        $rows = is_array($raw) ? self::clean_thresholds($raw) : null;
        return $rows && self::thresholds_error($rows, self::fail_mark($instance)) === '' ? $rows : self::DEFAULT_THRESHOLDS;
    }

    /**
     * The mark when no row fits.
     *
     * @param \stdClass $instance gns3lab record
     * @return int
     */
    public static function fail_mark(\stdClass $instance): int {
        $mark = $instance->checksfailmark ?? null;
        return is_numeric($mark) && (int) $mark >= 0 && (int) $mark <= self::MAX_MARK ? (int) $mark : self::DEFAULT_FAIL_MARK;
    }

    /**
     * Class overrides of an activity: code => class, unknown codes and classes dropped.
     *
     * @param \stdClass $instance gns3lab record
     * @return array
     */
    public static function overrides(\stdClass $instance): array {
        $raw = json_decode((string) ($instance->checksoverrides ?? ''), true);
        $out = [];
        foreach (is_array($raw) ? $raw : [] as $code => $class) {
            if (isset(self::RULES[$code]) && in_array($class, self::CLASSES, true)) {
                $out[$code] = $class;
            }
        }
        return $out;
    }

    /**
     * Most graded attempts of a student, 0 for unlimited.
     *
     * @param \stdClass $instance gns3lab record
     * @return int
     */
    public static function max_attempts(\stdClass $instance): int {
        return max(0, (int) ($instance->checksattempts ?? 0));
    }

    /**
     * How many graded attempts are left.
     *
     * @param \stdClass $instance gns3lab record
     * @param int $used graded attempts the student has had
     * @return int -1 for unlimited
     */
    public static function attempts_left(\stdClass $instance, int $used): int {
        $max = self::max_attempts($instance);
        return $max === 0 ? -1 : max(0, $max - $used);
    }

    /**
     * The report mode of an activity.
     *
     * @param \stdClass $instance gns3lab record
     * @return string full, counts or after
     */
    public static function report_mode(\stdClass $instance): string {
        $mode = (string) ($instance->checksreport ?? '');
        return in_array($mode, self::REPORTS, true) ? $mode : self::REPORT_FULL;
    }

    /**
     * From when the student sees the findings.
     *
     * @param \stdClass $instance gns3lab record
     * @return int 0 always, PHP_INT_MAX never (counts only), otherwise the time set in the activity
     */
    public static function details_from(\stdClass $instance): int {
        return match (self::report_mode($instance)) {
            self::REPORT_FULL => 0,
            self::REPORT_COUNTS => PHP_INT_MAX,
            default => max(0, (int) ($instance->checksreportdate ?? 0)),
        };
    }

    /**
     * Whether the findings of a run may be shown: always to a teacher who observes, to the student by the setting.
     *
     * @param \stdClass $instance gns3lab record
     * @param bool $observer the caller looks at the lab of another user (mod/gns3lab:observe checked)
     * @param int|null $now current time, for tests
     * @return bool
     */
    public static function details_visible(\stdClass $instance, bool $observer, ?int $now = null): bool {
        return $observer || ($now ?? time()) >= self::details_from($instance);
    }

    /**
     * The grading of the activity for POST /sessions/{id}/checks (CONTRACTS §2.9).
     *
     * Every field is sent, so the run stores exactly what the activity asked for.
     *
     * @param \stdClass $instance gns3lab record
     * @return array
     */
    public static function grading(\stdClass $instance): array {
        $overrides = self::overrides($instance);
        return [
            'profile' => self::profile($instance),
            // An empty PHP array would go as a JSON list: the manager expects an object.
            'overrides' => $overrides ? $overrides : new \stdClass(),
            'thresholds' => self::thresholds($instance),
            'fail_mark' => self::fail_mark($instance),
        ];
    }

    /**
     * Keeps the fields of the threshold rows and turns empty limits into null.
     *
     * @param array $rows rows as stored or submitted
     * @return array
     */
    public static function clean_thresholds(array $rows): array {
        $out = [];
        foreach ($rows as $row) {
            if (!is_array($row) || !isset($row['mark']) || $row['mark'] === '') {
                continue;
            }
            $clean = ['mark' => self::number($row['mark'])];
            foreach (['critical', 'major', 'minor'] as $class) {
                $value = $row[$class] ?? null;
                $clean[$class] = $value === null || $value === '' ? null : self::number($value);
            }
            $out[] = $clean;
        }
        return $out;
    }

    /**
     * What is wrong with thresholds, in the rules of the manager.
     *
     * @param array $rows rows from clean_thresholds()
     * @param int $failmark mark when no row fits
     * @return string language string key, empty if they are right
     */
    public static function thresholds_error(array $rows, int $failmark): string {
        if (!$rows || count($rows) > 10) {
            return 'checksthresholdsnone';
        }
        $previous = null;
        foreach ($rows as $row) {
            if (!is_int($row['mark']) || $row['mark'] < 0 || $row['mark'] > self::MAX_MARK) {
                return 'checksthresholdsmark';
            }
            foreach (['critical', 'major', 'minor'] as $class) {
                if ($row[$class] !== null && (!is_int($row[$class]) || $row[$class] < 0)) {
                    return 'checksthresholdslimit';
                }
            }
            if ($previous !== null && $row['mark'] >= $previous) {
                return 'checksthresholdsorder';
            }
            $previous = $row['mark'];
        }
        return $failmark < $previous ? '' : 'checksfailmarkhigh';
    }

    /**
     * Reads the class overrides of the activity form: one "code=class" per line, empty lines skipped.
     *
     * @param string $text text of the form field
     * @return array [array overrides code => class, string[] lines that are wrong]
     */
    public static function parse_overrides(string $text): array {
        $out = [];
        $bad = [];
        foreach (preg_split('/\R/', $text) as $line) {
            $line = trim($line);
            if ($line === '') {
                continue;
            }
            $parts = array_map('trim', explode('=', $line, 2));
            if (count($parts) !== 2 || !isset(self::RULES[$parts[0]]) || !in_array($parts[1], self::CLASSES, true)) {
                $bad[] = $line;
                continue;
            }
            $out[$parts[0]] = $parts[1];
        }
        return [$out, $bad];
    }

    /**
     * The overrides as the form shows them.
     *
     * @param array $overrides code => class
     * @return string
     */
    public static function format_overrides(array $overrides): string {
        $lines = [];
        foreach ($overrides as $code => $class) {
            $lines[] = "{$code}={$class}";
        }
        return implode("\n", $lines);
    }

    /**
     * The gradebook value of a mark of the manager.
     *
     * Points: the share of the top mark of the run's thresholds, mark / top × maximum grade. A scale of N items: the
     * marks from the fail mark to the top mark spread evenly over the items 1…N (5/4/3/2 on a scale of four items is
     * one item per mark).
     *
     * @param int $mark mark of the run
     * @param int $topmark top mark of the run's thresholds
     * @param int $failmark fail mark of the run
     * @param int $grade grade setting of the activity: points if positive, minus a scale id if negative
     * @param int $scaleitems number of items of that scale
     * @return float|null null if the activity has no grade
     */
    public static function grade_value(int $mark, int $topmark, int $failmark, int $grade, int $scaleitems = 0): ?float {
        if ($grade > 0) {
            return $topmark > 0 ? round(min(1, max(0, $mark / $topmark)) * $grade, 5) : 0.0;
        }
        if ($grade < 0 && $scaleitems > 0) {
            if ($topmark <= $failmark) {
                return (float) $scaleitems;
            }
            $share = min(1, max(0, ($mark - $failmark) / ($topmark - $failmark)));
            return (float) (1 + (int) round($share * ($scaleitems - 1)));
        }
        return null;
    }

    /**
     * An integer from a form value, or the value itself when it is not one (the check then refuses it).
     *
     * @param mixed $value submitted value
     * @return mixed
     */
    protected static function number(mixed $value): mixed {
        if (is_int($value)) {
            return $value;
        }
        return is_string($value) && preg_match('/^\s*\d+\s*$/', $value) ? (int) $value : $value;
    }
}
