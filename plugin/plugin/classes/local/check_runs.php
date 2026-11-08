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

use core_external\external_multiple_structure;
use core_external\external_single_structure;
use core_external\external_value;

/**
 * Check runs of the students of an activity (table gns3lab_check_runs, docs/autochecks.md).
 *
 * The lab manager keeps a run with its session and deletes it with the session (CONTRACTS §2.9); the plugin keeps
 * what it needs beyond that: whose run it is, the outcome, the mark and the counts. That is what counts the attempts
 * and what the gradebook is computed from. The findings are not copied: they are read from the manager when the
 * report is opened.
 *
 * An attempt is a run that ended with a mark (status done, outcome graded). A run that failed or was incomplete
 * neither counts as an attempt nor changes the grade. The grade is that of the last graded run.
 *
 * @package    mod_gns3lab
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
class check_runs {
    /** Table. */
    public const TABLE = 'gns3lab_check_runs';

    /** Statuses of a run that is not over. */
    public const PENDING = ['queued', 'running'];

    /** A run the manager no longer knows (its session is gone) and that never finished. */
    public const LOST = 'lost';

    /** A run still pending after this long is given up, seconds (the manager fails it much sooner). */
    public const GIVE_UP = DAYSECS;

    /**
     * Records a run the manager has just accepted, or finds the one already recorded.
     *
     * @param \stdClass $instance gns3lab record
     * @param int $userid owner of the lab
     * @param string $sessionid session of the run
     * @param \stdClass $run run from the manager
     * @param int $requestedby user who asked for the check
     * @return \stdClass row
     */
    public static function record(
        \stdClass $instance,
        int $userid,
        string $sessionid,
        \stdClass $run,
        int $requestedby
    ): \stdClass {
        global $DB;

        $row = $DB->get_record(self::TABLE, ['gns3labid' => $instance->id, 'runid' => $run->run_id]);
        if (!$row) {
            $now = time();
            $row = (object) [
                'gns3labid' => (int) $instance->id,
                'userid' => $userid,
                'sessionid' => \core_text::substr($sessionid, 0, 64),
                'runid' => $run->run_id,
                'requestedby' => $requestedby,
                'status' => 'queued',
                'outcome' => '',
                'mark' => null,
                'topmark' => 0,
                'failmark' => 0,
                'critical' => 0,
                'major' => 0,
                'minor' => 0,
                'note' => 0,
                'errorcode' => '',
                'timecreated' => $now,
                'timemodified' => $now,
                'timefinished' => 0,
            ];
            $row->id = $DB->insert_record(self::TABLE, $row);
        }
        return self::apply($instance, $row, $run);
    }

    /**
     * Takes the state of a run from the manager; a run that has just been graded updates the gradebook.
     *
     * @param \stdClass $instance gns3lab record
     * @param \stdClass $row row of the run
     * @param \stdClass $run run from the manager
     * @return \stdClass the row as it is now
     */
    public static function apply(\stdClass $instance, \stdClass $row, \stdClass $run): \stdClass {
        global $DB;

        $status = in_array($run->status ?? '', ['queued', 'running', 'done', 'failed'], true) ? $run->status : $row->status;
        $new = clone $row;
        $new->status = $status;
        $result = ($run->result ?? null) instanceof \stdClass ? $run->result : null;
        if ($status === 'done' && $result) {
            $new->outcome = in_array($result->outcome ?? '', ['graded', 'incomplete'], true) ? $result->outcome : '';
            $new->mark = $new->outcome === 'graded' && is_int($result->mark ?? null) ? $result->mark : null;
            if ($new->outcome === 'graded' && $new->mark === null) {
                $new->outcome = '';
            }
            foreach (checks::CLASSES as $class) {
                $count = $result->counts->{$class} ?? 0;
                $new->{$class} = is_int($count) && $count >= 0 ? $count : 0;
            }
            [$new->topmark, $new->failmark] = self::scale_of($run);
        }
        if ($status === 'failed') {
            $code = $run->error->code ?? '';
            $new->errorcode = is_string($code) ? \core_text::substr(preg_replace('/[^A-Za-z0-9_]/', '', $code), 0, 32) : '';
        }
        if (!in_array($status, self::PENDING, true) && !$row->timefinished) {
            $finished = is_string($run->finished_at ?? null) ? strtotime($run->finished_at) : false;
            $new->timefinished = $finished ?: time();
        }
        if ($new == $row) {
            return $row;
        }
        $new->timemodified = time();
        $DB->update_record(self::TABLE, $new);
        if (self::is_graded($new) && !self::is_graded($row)) {
            self::update_grades($instance, (int) $row->userid);
        }
        return $new;
    }

    /**
     * Asks the manager about the runs of a user that are not over yet and records what changed.
     *
     * A run the manager no longer knows (its session was deleted) becomes failed with the code "lost". A manager that
     * does not answer leaves the rows as they are: the next call tries again.
     *
     * @param \stdClass $instance gns3lab record
     * @param int $userid owner of the lab
     * @param manager_client $client manager client
     */
    public static function sync(\stdClass $instance, int $userid, manager_client $client): void {
        global $DB;

        [$insql, $params] = $DB->get_in_or_equal(self::PENDING, SQL_PARAMS_NAMED);
        $rows = $DB->get_records_select(
            self::TABLE,
            "gns3labid = :labid AND userid = :userid AND status {$insql}",
            $params + ['labid' => $instance->id, 'userid' => $userid],
            'id'
        );
        foreach ($rows as $row) {
            self::refresh($instance, $row, $client);
        }
    }

    /**
     * Reads one run from the manager and records it.
     *
     * @param \stdClass $instance gns3lab record
     * @param \stdClass $row row of the run
     * @param manager_client $client manager client
     * @return array [\stdClass row, \stdClass|null the whole run, null if the manager no longer has it]
     */
    public static function refresh(\stdClass $instance, \stdClass $row, manager_client $client): array {
        try {
            $run = $client->get_check($row->sessionid, $row->runid);
        } catch (manager_exception $e) {
            if (!$e->is_not_found()) {
                throw $e;
            }
            if (in_array($row->status, self::PENDING, true)) {
                $row = self::apply($instance, $row, (object) ['status' => 'failed', 'error' => (object) ['code' => self::LOST]]);
            }
            return [$row, null];
        }
        return [self::apply($instance, $row, $run), $run];
    }

    /**
     * Gives up runs that stayed pending far longer than the manager allows; used by the scheduled task.
     *
     * @param int $now current time
     * @return int how many rows changed
     */
    public static function give_up_stale(int $now): int {
        global $DB;

        [$insql, $params] = $DB->get_in_or_equal(self::PENDING, SQL_PARAMS_NAMED);
        $params['before'] = $now - self::GIVE_UP;
        $count = $DB->count_records_select(self::TABLE, "status {$insql} AND timecreated < :before", $params);
        if ($count) {
            $DB->execute(
                "UPDATE {" . self::TABLE . "} SET status = 'failed', errorcode = :code, timemodified = :now, timefinished = :now2
                  WHERE status {$insql} AND timecreated < :before",
                $params + ['code' => self::LOST, 'now' => $now, 'now2' => $now]
            );
        }
        return $count;
    }

    /**
     * A run of a user in an activity.
     *
     * @param int $instanceid gns3lab id
     * @param int $userid owner of the lab
     * @param string $runid run id
     * @return \stdClass|null
     */
    public static function get(int $instanceid, int $userid, string $runid): ?\stdClass {
        global $DB;

        return $DB->get_record(self::TABLE, ['gns3labid' => $instanceid, 'userid' => $userid, 'runid' => $runid]) ?: null;
    }

    /**
     * Runs of a user in an activity, the newest first.
     *
     * @param int $instanceid gns3lab id
     * @param int $userid owner of the lab
     * @return \stdClass[]
     */
    public static function rows(int $instanceid, int $userid): array {
        global $DB;

        return array_values($DB->get_records(self::TABLE, ['gns3labid' => $instanceid, 'userid' => $userid], 'id DESC'));
    }

    /**
     * The pending run of a session, if there is one.
     *
     * @param int $instanceid gns3lab id
     * @param int $userid owner of the lab
     * @param string $sessionid session id
     * @return \stdClass|null
     */
    public static function pending(int $instanceid, int $userid, string $sessionid): ?\stdClass {
        foreach (self::rows($instanceid, $userid) as $row) {
            if ($row->sessionid === $sessionid && in_array($row->status, self::PENDING, true)) {
                return $row;
            }
        }
        return null;
    }

    /**
     * Graded attempts of a user in an activity.
     *
     * @param int $instanceid gns3lab id
     * @param int $userid owner of the lab
     * @return int
     */
    public static function attempts_used(int $instanceid, int $userid): int {
        global $DB;

        return $DB->count_records(self::TABLE, [
            'gns3labid' => $instanceid, 'userid' => $userid, 'status' => 'done', 'outcome' => 'graded',
        ]);
    }

    /**
     * Gradebook values of the users of an activity: the last graded run of each.
     *
     * @param \stdClass $instance gns3lab record with grade and course
     * @param int $userid one user, 0 for all
     * @return array userid => grade object (userid, rawgrade, dategraded, datesubmitted, feedback, feedbackformat)
     */
    public static function user_grades(\stdClass $instance, int $userid = 0): array {
        global $DB;

        $grade = (int) ($instance->grade ?? 0);
        $items = 0;
        if ($grade < 0) {
            $scale = $DB->get_field('scale', 'scale', ['id' => -$grade]);
            $items = $scale ? count(explode(',', $scale)) : 0;
        }
        $params = ['labid' => $instance->id, 'status' => 'done', 'outcome' => 'graded'];
        $where = 'gns3labid = :labid AND status = :status AND outcome = :outcome';
        if ($userid) {
            $where .= ' AND userid = :userid';
            $params['userid'] = $userid;
        }
        $out = [];
        foreach ($DB->get_records_select(self::TABLE, $where, $params, 'id ASC') as $row) {
            $value = checks::grade_value((int) $row->mark, (int) $row->topmark, (int) $row->failmark, $grade, $items);
            if ($value === null) {
                continue;
            }
            // Ordered by id: a later run of the user replaces an earlier one.
            $out[(int) $row->userid] = (object) [
                'userid' => (int) $row->userid,
                'rawgrade' => $value,
                'dategraded' => (int) ($row->timefinished ?: $row->timemodified),
                'datesubmitted' => (int) $row->timecreated,
                'feedback' => get_string('checksfeedback', 'gns3lab', (object) [
                    'mark' => $row->mark, 'critical' => $row->critical, 'major' => $row->major, 'minor' => $row->minor,
                    'note' => $row->note,
                ]),
                'feedbackformat' => FORMAT_PLAIN,
            ];
        }
        return $out;
    }

    /**
     * Deletes the runs of an activity, of some users or of all.
     *
     * @param int $instanceid gns3lab id
     * @param int[]|null $userids users, null for everyone
     */
    public static function delete(int $instanceid, ?array $userids = null): void {
        global $DB;

        if ($userids === null) {
            $DB->delete_records(self::TABLE, ['gns3labid' => $instanceid]);
            return;
        }
        if (!$userids) {
            return;
        }
        [$insql, $params] = $DB->get_in_or_equal($userids, SQL_PARAMS_NAMED);
        $DB->delete_records_select(self::TABLE, "gns3labid = :labid AND userid {$insql}", $params + ['labid' => $instanceid]);
    }

    /**
     * Short description of a run for the external functions.
     *
     * @param \stdClass $row row of the run
     * @return array
     */
    public static function export(\stdClass $row): array {
        return [
            'runid' => $row->runid,
            'status' => $row->status,
            'outcome' => (string) $row->outcome,
            'mark' => $row->mark === null ? -1 : (int) $row->mark,
            'counts' => [
                'critical' => (int) $row->critical,
                'major' => (int) $row->major,
                'minor' => (int) $row->minor,
                'note' => (int) $row->note,
            ],
            'errorcode' => (string) $row->errorcode,
            'requestedat' => (int) $row->timecreated,
            'finishedat' => (int) $row->timefinished,
        ];
    }

    /**
     * Description of export() for execute_returns().
     *
     * @return array fields of external_single_structure
     */
    public static function summary_fields(): array {
        return [
            'runid' => new external_value(PARAM_ALPHANUMEXT, 'Run id on the lab manager'),
            'status' => new external_value(PARAM_ALPHA, 'queued, running, done or failed'),
            'outcome' => new external_value(PARAM_ALPHA, 'graded or incomplete for a done run, empty otherwise'),
            'mark' => new external_value(PARAM_INT, 'Mark of a graded run, -1 otherwise'),
            'counts' => new external_single_structure([
                'critical' => new external_value(PARAM_INT, 'Counted critical findings'),
                'major' => new external_value(PARAM_INT, 'Counted major findings'),
                'minor' => new external_value(PARAM_INT, 'Counted minor findings'),
                'note' => new external_value(PARAM_INT, 'Forgiven findings (notes)'),
            ]),
            'errorcode' => new external_value(PARAM_ALPHANUMEXT, 'Why a failed run failed, empty otherwise'),
            'requestedat' => new external_value(PARAM_INT, 'When the check was asked for'),
            'finishedat' => new external_value(PARAM_INT, 'When the run ended, 0 while it goes on'),
        ];
    }

    /**
     * The findings and rules of a run from the manager, for the external functions.
     *
     * @param \stdClass|null $run run from the manager
     * @return array ['findings' => array, 'rules' => array]
     */
    public static function export_details(?\stdClass $run): array {
        $result = $run && ($run->result ?? null) instanceof \stdClass ? $run->result : null;
        $findings = [];
        foreach (is_array($result->findings ?? null) ? $result->findings : [] as $f) {
            if (!$f instanceof \stdClass || !is_string($f->code ?? null)) {
                continue;
            }
            $ifaces = [];
            foreach (is_array($f->ifaces ?? null) ? $f->ifaces : [] as $iface) {
                if ($iface instanceof \stdClass && is_string($iface->node ?? null) && is_string($iface->iface ?? null)) {
                    $ifaces[] = ['node' => $iface->node, 'iface' => $iface->iface];
                }
            }
            $findings[] = [
                'id' => self::token($f->id ?? ''),
                'code' => self::token($f->code),
                'group' => self::token($f->group ?? ''),
                'class' => in_array($f->class ?? '', checks::CLASSES, true) ? $f->class : 'note',
                'baseclass' => in_array($f->base_class ?? '', checks::CLASSES, true) ? $f->base_class : '',
                'counted' => ($f->counted ?? false) === true,
                'causedby' => is_string($f->caused_by ?? null) ? self::token($f->caused_by) : '',
                'nodes' => array_values(array_filter(is_array($f->nodes ?? null) ? $f->nodes : [], 'is_string')),
                'ifaces' => $ifaces,
                'message' => is_string($f->message ?? null) ? $f->message : '',
            ];
        }
        $rules = [];
        foreach (is_array($result->rules ?? null) ? $result->rules : [] as $r) {
            if ($r instanceof \stdClass && is_string($r->code ?? null)) {
                $rules[] = [
                    'code' => self::token($r->code),
                    'group' => self::token($r->group ?? ''),
                    'status' => in_array($r->status ?? '', ['pass', 'fail', 'skipped', 'off'], true) ? $r->status : '',
                    'reason' => self::token($r->reason ?? ''),
                ];
            }
        }
        return ['findings' => $findings, 'rules' => $rules];
    }

    /**
     * Description of export_details() for execute_returns().
     *
     * @return array fields of external_single_structure
     */
    public static function details_fields(): array {
        return [
            'findings' => new external_multiple_structure(new external_single_structure([
                'id' => new external_value(PARAM_ALPHANUMEXT, 'Finding id inside the run'),
                'code' => new external_value(PARAM_ALPHANUMEXT, 'Rule code'),
                'group' => new external_value(PARAM_ALPHANUMEXT, 'Rule group: l1, l2, addressing, gateways, routing, host'),
                'class' => new external_value(PARAM_ALPHA, 'critical, major, minor or note'),
                'baseclass' => new external_value(PARAM_ALPHA, 'Class by the profile, before overrides and the focus'),
                'counted' => new external_value(PARAM_BOOL, 'Counts towards the mark (not a consequence, not a note)'),
                'causedby' => new external_value(PARAM_ALPHANUMEXT, 'Id of the root cause finding, empty if none'),
                'nodes' => new external_multiple_structure(new external_value(PARAM_TEXT, 'Node name')),
                'ifaces' => new external_multiple_structure(new external_single_structure([
                    'node' => new external_value(PARAM_TEXT, 'Node name'),
                    'iface' => new external_value(PARAM_TEXT, 'Interface name on the device'),
                ])),
                'message' => new external_value(PARAM_TEXT, 'Text of the finding from the lab manager (in Russian)'),
            ]), 'Findings, empty when the report is not shown'),
            'rules' => new external_multiple_structure(new external_single_structure([
                'code' => new external_value(PARAM_ALPHANUMEXT, 'Rule code'),
                'group' => new external_value(PARAM_ALPHANUMEXT, 'Rule group'),
                'status' => new external_value(PARAM_ALPHA, 'pass, fail, skipped or off'),
                'reason' => new external_value(PARAM_ALPHANUMEXT, 'Why a rule was skipped, empty otherwise'),
            ]), 'Rules of the lab, empty when the report is not shown'),
        ];
    }

    /**
     * Whether a row is a graded attempt.
     *
     * @param \stdClass $row row of the run
     * @return bool
     */
    public static function is_graded(\stdClass $row): bool {
        return $row->status === 'done' && $row->outcome === 'graded' && $row->mark !== null;
    }

    /**
     * Top mark and fail mark of the grading the run was graded with.
     *
     * @param \stdClass $run run from the manager
     * @return int[] [top mark, fail mark]
     */
    protected static function scale_of(\stdClass $run): array {
        $grading = ($run->grading ?? null) instanceof \stdClass ? $run->grading : new \stdClass();
        $marks = [];
        foreach (is_array($grading->thresholds ?? null) ? $grading->thresholds : [] as $row) {
            if (isset($row->mark) && is_int($row->mark)) {
                $marks[] = $row->mark;
            }
        }
        $top = $marks ? max($marks) : checks::DEFAULT_THRESHOLDS[0]['mark'];
        $fail = is_int($grading->fail_mark ?? null) ? $grading->fail_mark : checks::DEFAULT_FAIL_MARK;
        return [$top, $fail];
    }

    /**
     * Updates the gradebook of one user.
     *
     * @param \stdClass $instance gns3lab record
     * @param int $userid user
     */
    protected static function update_grades(\stdClass $instance, int $userid): void {
        global $CFG;

        require_once($CFG->dirroot . '/mod/gns3lab/lib.php');
        gns3lab_update_grades($instance, $userid);
    }

    /**
     * A code-like string of the manager, cut to what PARAM_ALPHANUMEXT keeps.
     *
     * @param mixed $value value
     * @return string
     */
    protected static function token(mixed $value): string {
        return is_string($value) ? \core_text::substr(preg_replace('/[^A-Za-z0-9_-]/', '', $value), 0, 64) : '';
    }
}
