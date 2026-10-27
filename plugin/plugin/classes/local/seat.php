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
 * The seat: who may drive a student's lab right now (docs/seat.md).
 *
 * A lab belongs to one student, but nothing in Moodle stops a second person who knows the password from signing in
 * with the same account and working in the same lab at the same time (the "evil twin"). The seat is the answer. At
 * any moment one browser page holds the seat of a student in an activity, and only the holder may start or stop the
 * lab, open a console or write the uncovered mask. The others see a wall that tells them why.
 *
 * What counts as "the same holder" is a setting of the activity (`seatmode`):
 *
 * - `tab` (default): one browser page. A newer page of the same Moodle login takes the seat over (the older page is
 *   told and closes its consoles, the student can take it back with a button); another Moodle login never does.
 * - `login`: one Moodle login. Any number of pages of the same login share the seat; another login is refused.
 * - `off`: no limit. For labs that are meant to be shared (nothing else changes: the lab is still the student's).
 *
 * A login is told from another by the hash of the Moodle session id. A page is told from another by a random id the
 * browser makes on every page load; it is not a secret and proves nothing by itself, it only separates the tabs of
 * one person. The seat is alive for `TTL` seconds after the last time the holder showed up (a heartbeat of the page,
 * or any call that needs the seat), so a closed browser frees it without any action.
 *
 * The seat does not stop the holder from giving the password away and signing out: it stops two people from working
 * at the same moment. Reading the state of the lab (get_state, get_facts) is not behind the seat; the consoles and
 * every change are.
 *
 * @package    mod_gns3lab
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
class seat {
    /** One browser page holds the seat; a newer page of the same login takes it over. */
    public const TAB = 'tab';

    /** One Moodle login holds the seat, whatever number of pages it has open. */
    public const LOGIN = 'login';

    /** The seat is not checked. */
    public const OFF = 'off';

    /** The caller holds the seat (or the activity does not check it). */
    public const HELD = 'held';

    /** Another page of the same login holds the seat (mode `tab`). */
    public const OTHER_TAB = 'othertab';

    /** Another login of the same account holds the seat. */
    public const OTHER_LOGIN = 'otherlogin';

    /**
     * Seconds the seat stays with its holder after the last sign of life.
     *
     * Longer than a minute on purpose: a browser throttles the timers of a page that is in the background for a long
     * time to one run a minute, and the seat must survive that. A page that was frozen for longer takes the seat back
     * by itself at the next beat, if nobody took it meanwhile.
     */
    public const TTL = 90;

    /** Seconds between the beats of a page (the page gets this number from the server). */
    public const BEAT = 20;

    /** Longest page id; the column of the table is as long. */
    public const TABID_MAX = 64;

    /**
     * Modes in the order of the activity form.
     *
     * @return string[]
     */
    public static function modes(): array {
        return [self::TAB, self::LOGIN, self::OFF];
    }

    /**
     * The mode of an activity; a value nobody knows counts as the strictest, `tab`.
     *
     * @param \stdClass $instance gns3lab record
     * @return string
     */
    public static function mode(\stdClass $instance): string {
        $mode = (string) ($instance->seatmode ?? self::TAB);
        return in_array($mode, self::modes(), true) ? $mode : self::TAB;
    }

    /**
     * Identity of the Moodle login of the current request: the hash of the session id.
     *
     * The session id itself is a credential and is not stored. Without a web session (CLI, tests) the session key of
     * the user stands in for it.
     *
     * @return string 64 hex digits
     */
    public static function session_key(): string {
        $sid = session_id();
        if (!is_string($sid) || $sid === '') {
            $sid = sesskey();
        }
        return hash('sha256', $sid);
    }

    /**
     * Checks the page id from the browser.
     *
     * @param string $tabid page id, empty from a page that does not send one
     * @return string the same id
     * @throws \invalid_parameter_exception when it does not fit the column
     */
    public static function clean_tabid(string $tabid): string {
        if (\core_text::strlen($tabid) > self::TABID_MAX) {
            throw new \invalid_parameter_exception('tabid is too long');
        }
        return $tabid;
    }

    /**
     * Who gets the seat, as a pure decision.
     *
     * @param string $mode seat mode of the activity
     * @param \stdClass|null $row the seat as stored, null if nobody has it
     * @param string $sessionkey login of the caller
     * @param string $tabid page of the caller
     * @param bool $takeover the caller is a page that was just opened or the student pressed "use here": a page of
     *      the same login may then take the seat from another page. A beat never takes over.
     * @param int $now current time
     * @return string HELD, OTHER_TAB or OTHER_LOGIN
     */
    public static function verdict(
        string $mode,
        ?\stdClass $row,
        string $sessionkey,
        string $tabid,
        bool $takeover,
        int $now
    ): string {
        if ($mode === self::OFF || $row === null || $now - (int) $row->timemodified > self::TTL) {
            return self::HELD;
        }
        if (!hash_equals((string) $row->sessionkey, $sessionkey)) {
            // Another login never takes the seat over, "use here" or not: the one who works keeps it.
            return self::OTHER_LOGIN;
        }
        if ($mode === self::LOGIN || $takeover || (string) $row->tabid === $tabid) {
            return self::HELD;
        }
        return self::OTHER_TAB;
    }

    /**
     * Claims the seat for the caller, or reports who has it. Giving the seat to its holder is a beat: it keeps it alive.
     *
     * @param \stdClass $instance gns3lab record
     * @param int $userid owner of the lab
     * @param string $tabid page of the caller
     * @param bool $takeover see verdict()
     * @param string|null $sessionkey login of the caller, null for the current request
     * @param int|null $now current time, null for now
     * @return array `status` (HELD, OTHER_TAB, OTHER_LOGIN) and `idle`, seconds since the holder last showed up (0 when held)
     */
    public static function claim(
        \stdClass $instance,
        int $userid,
        string $tabid,
        bool $takeover = false,
        ?string $sessionkey = null,
        ?int $now = null
    ): array {
        global $DB;

        $mode = self::mode($instance);
        if ($mode === self::OFF) {
            return ['status' => self::HELD, 'idle' => 0];
        }
        $sessionkey ??= self::session_key();
        $now ??= time();
        $key = ['gns3labid' => (int) $instance->id, 'userid' => $userid];

        // Most beats come from the holder and change nothing but the time: no lock, no write until a third of the
        // lifetime has passed.
        $row = $DB->get_record('gns3lab_seat', $key) ?: null;
        if (
            $row !== null && $now - (int) $row->timemodified < intdiv(self::TTL, 3)
            && hash_equals((string) $row->sessionkey, $sessionkey) && (string) $row->tabid === $tabid
        ) {
            return ['status' => self::HELD, 'idle' => 0];
        }

        // The decision and the write go together, or two pages could both be told they hold the seat.
        $lock = \core\lock\lock_config::get_lock_factory('mod_gns3lab_seat')->get_lock(
            'seat_' . $key['gns3labid'] . '_' . $userid,
            5
        );
        if (!$lock) {
            throw new \moodle_exception('seatbusy', 'gns3lab');
        }
        try {
            $row = $DB->get_record('gns3lab_seat', $key) ?: null;
            $status = self::verdict($mode, $row, $sessionkey, $tabid, $takeover, $now);
            if ($status === self::HELD) {
                self::write($row, $key, $sessionkey, $tabid, $now);
            }
        } finally {
            $lock->release();
        }
        // A refusal always has a row behind it: without one the seat is free.
        $idle = $status === self::HELD ? 0 : max(0, $now - (int) $row->timemodified);
        return ['status' => $status, 'idle' => $idle];
    }

    /**
     * Gives the seat to the caller in the table.
     *
     * @param \stdClass|null $row current row, null if there is none
     * @param array $key gns3labid and userid
     * @param string $sessionkey login of the caller
     * @param string $tabid page of the caller
     * @param int $now current time
     */
    protected static function write(?\stdClass $row, array $key, string $sessionkey, string $tabid, int $now): void {
        global $DB;

        if ($row === null) {
            $DB->insert_record('gns3lab_seat', (object) ($key + [
                'sessionkey' => $sessionkey,
                'tabid' => $tabid,
                'timeclaimed' => $now,
                'timemodified' => $now,
            ]));
            return;
        }
        $changed = !hash_equals((string) $row->sessionkey, $sessionkey) || (string) $row->tabid !== $tabid;
        $row->sessionkey = $sessionkey;
        $row->tabid = $tabid;
        $row->timeclaimed = $changed ? $now : $row->timeclaimed;
        $row->timemodified = $now;
        $DB->update_record('gns3lab_seat', $row);
    }

    /**
     * Lets the caller through when it holds the seat. For everything that drives the student's own lab.
     *
     * The caller claims the seat on the way (without taking it over from another page), so a request that arrives
     * before the first beat of the page, or after the seat expired and nobody took it, still works.
     *
     * @param lab_access $access the activity
     * @param int $userid owner of the lab (the current user: an observer never needs the seat)
     * @param string $tabid page of the caller, empty from a page that does not send one
     * @throws \moodle_exception seatotherlogin or seatothertab
     */
    public static function require_seat(lab_access $access, int $userid, string $tabid): void {
        $result = self::claim($access->instance, $userid, self::clean_tabid($tabid));
        if ($result['status'] === self::HELD) {
            return;
        }
        self::log_denied($access, $userid, $result['status'], 'action');
        throw new \moodle_exception($result['status'] === self::OTHER_LOGIN ? 'seatotherlogin' : 'seatothertab', 'gns3lab');
    }

    /**
     * Writes a refusal to the log when it may mean a second person on the account: another login asked.
     *
     * A refusal because of another tab of the same login is routine and not logged.
     *
     * @param lab_access $access the activity
     * @param int $userid owner of the lab
     * @param string $status OTHER_TAB or OTHER_LOGIN
     * @param string $where `page` (a page asked for the seat) or `action` (a call that needs it)
     */
    public static function log_denied(lab_access $access, int $userid, string $status, string $where): void {
        if ($status !== self::OTHER_LOGIN) {
            return;
        }
        \mod_gns3lab\event\seat_denied::create([
            'objectid' => $access->instance->id,
            'context' => $access->context,
            'relateduserid' => $userid,
            'other' => ['mode' => self::mode($access->instance), 'where' => $where],
        ])->trigger();
    }

    /**
     * Deletes the seats of an activity (all users) or of some users in it.
     *
     * @param int $labid gns3lab id
     * @param int[]|null $userids users, null for all
     */
    public static function delete(int $labid, ?array $userids = null): void {
        global $DB;

        if ($userids === null) {
            $DB->delete_records('gns3lab_seat', ['gns3labid' => $labid]);
            return;
        }
        if (!$userids) {
            return;
        }
        [$insql, $params] = $DB->get_in_or_equal($userids, SQL_PARAMS_NAMED);
        $DB->delete_records_select('gns3lab_seat', "gns3labid = :labid AND userid $insql", ['labid' => $labid] + $params);
    }
}
