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
 * Busy slots of the site for its administrator (slots.php): who holds which slot and with which lab.
 *
 * The manager knows Moodle ids only (CONTRACTS §2.13); login, name, course, activity and lab name come from this site.
 * Callers check moodle/site:config.
 *
 * @package    mod_gns3lab
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
class live_slots {
    /** Feature of the manager that has GET /live-sessions. */
    public const FEATURE = 'live-sessions';

    /**
     * Live sessions of the site as rows of the page, and the counts of the slots.
     *
     * @param manager_client $client
     * @return array{slots: \stdClass, rows: array[]}
     */
    public static function load(manager_client $client): array {
        $data = $client->list_live_sessions();
        return ['slots' => $data->slots, 'rows' => self::rows($data->sessions)];
    }

    /**
     * Whether a session id is one of the live sessions of this site now: the stop button names no other.
     *
     * @param manager_client $client
     * @param string $sessionid
     * @return bool
     */
    public static function is_live(manager_client $client, string $sessionid): bool {
        foreach ($client->list_live_sessions()->sessions as $session) {
            if ($session->session_id === $sessionid) {
                return true;
            }
        }
        return false;
    }

    /**
     * Rows for the template, in the order of the manager (slots first, then the queue).
     *
     * @param \stdClass[] $sessions live sessions of the manager
     * @return array[]
     */
    public static function rows(array $sessions): array {
        global $DB;

        $userids = array_unique(array_map(fn($s) => (int) $s->user_id, $sessions));
        $users = [];
        if ($userids) {
            $fields = \core_user\fields::for_name()->including('username', 'email', 'deleted')
                ->get_sql('', false, '', '', false)->selects;
            $users = $DB->get_records_list('user', 'id', $userids, '', 'id, ' . $fields);
        }
        $librarylabs = [];
        $libraryids = array_filter(array_map(fn($s) => (int) ($s->library_lab_id ?? 0), $sessions));
        if ($libraryids) {
            $librarylabs = $DB->get_records_list('gns3lab_labs', 'id', array_unique($libraryids), '', 'id, name');
        }

        $rows = [];
        $format = get_string('strftimedatetimeshort', 'core_langconfig');
        foreach ($sessions as $session) {
            $user = $users[(int) $session->user_id] ?? null;
            $row = [
                'sessionid' => $session->session_id,
                'slot' => isset($session->slot_vmid) && is_int($session->slot_vmid) ? $session->slot_vmid : null,
                'userid' => (int) $session->user_id,
                'username' => $user && !$user->deleted ? $user->username : '',
                'fullname' => $user && !$user->deleted ? fullname($user) : get_string('liveslotsunknownuser', 'gns3lab'),
                'email' => $user && !$user->deleted ? (string) ($user->email ?? '') : '',
                'profileurl' => $user && !$user->deleted
                    ? (new \core\url('/user/profile.php', ['id' => $user->id]))->out(false) : null,
                'status' => self::status_text($session),
                'statusclass' => match ($session->status) {
                    'running' => 'bg-success',
                    'queued', 'starting' => 'bg-info text-dark',
                    default => 'bg-secondary',
                },
                'authoring' => ($session->mode ?? null) === 'authoring',
                'started' => self::time($session->started_at ?? null, $format),
                'expires' => self::time($session->expires_at ?? null, $format),
                'created' => self::time($session->created_at ?? null, $format),
                'stoppable' => $session->status !== 'stopping',
                'course' => null,
                'courseurl' => null,
                'activity' => null,
                'activityurl' => null,
                'observeurl' => null,
                'lab' => '',
                'labdetail' => (string) ($session->lab_ref ?? ''),
            ];
            if ($row['authoring']) {
                $lab = $librarylabs[(int) ($session->library_lab_id ?? 0)] ?? null;
                $row['lab'] = $lab ? format_string($lab->name, true, ['context' => \context_system::instance()])
                    : get_string('liveslotsunknownlab', 'gns3lab');
                if ($lab) {
                    $row['activityurl'] = (new \core\url('/mod/gns3lab/editor.php', ['id' => $lab->id]))->out(false);
                }
                $row['activity'] = get_string('liveslotsauthoring', 'gns3lab');
            } else {
                self::add_activity($row, (int) ($session->instance_id ?? 0), (int) ($session->course_id ?? 0));
            }
            $rows[] = $row;
        }
        return $rows;
    }

    /**
     * Course, activity, lab name and the observer link of a session of an activity.
     *
     * @param array $row row to fill
     * @param int $instanceid gns3lab instance id
     * @param int $courseid course id the manager was given, for a deleted activity
     */
    protected static function add_activity(array &$row, int $instanceid, int $courseid): void {
        global $DB;

        $cm = $instanceid ? get_coursemodule_from_instance('gns3lab', $instanceid, 0, false, IGNORE_MISSING) : false;
        $courseid = $cm ? (int) $cm->course : $courseid;
        $course = $courseid ? $DB->get_record('course', ['id' => $courseid], 'id, fullname, shortname') : false;
        if ($course) {
            $context = \context_course::instance($course->id);
            $row['course'] = format_string($course->shortname, true, ['context' => $context]);
            $row['courseurl'] = (new \core\url('/course/view.php', ['id' => $course->id]))->out(false);
        }
        if (!$cm) {
            $row['activity'] = get_string('liveslotsdeletedactivity', 'gns3lab', $instanceid);
            $row['lab'] = $row['labdetail'];
            $row['labdetail'] = '';
            return;
        }
        $context = \context_module::instance($cm->id);
        $row['activity'] = format_string($cm->name, true, ['context' => $context]);
        $row['activityurl'] = (new \core\url('/mod/gns3lab/view.php', ['id' => $cm->id]))->out(false);
        $row['observeurl'] = (new \core\url('/mod/gns3lab/lab.php', ['id' => $cm->id, 'userid' => $row['userid']]))->out(false);
        $instance = $DB->get_record('gns3lab', ['id' => $instanceid], 'id, labref, labversionid');
        $name = $instance && $instance->labversionid ? $DB->get_field_sql(
            'SELECT l.name FROM {gns3lab_lab_versions} v JOIN {gns3lab_labs} l ON l.id = v.labid WHERE v.id = ?',
            [$instance->labversionid]
        ) : false;
        if ($name !== false) {
            $row['lab'] = format_string($name, true, ['context' => \context_system::instance()]);
        } else {
            $row['lab'] = $row['labdetail'];
            $row['labdetail'] = '';
        }
    }

    /**
     * Status of a session in words: the stage when there is a string for it, else the status.
     *
     * @param \stdClass $session
     * @return string
     */
    protected static function status_text(\stdClass $session): string {
        $manager = get_string_manager();
        foreach (['stage_' . ($session->stage ?? ''), 'status_' . $session->status] as $key) {
            if ($manager->string_exists($key, 'gns3lab')) {
                return get_string($key, 'gns3lab');
            }
        }
        return $session->status;
    }

    /**
     * A time of the manager (ISO 8601) in the user's time zone, or null.
     *
     * @param mixed $value
     * @param string $format strftime format
     * @return string|null
     */
    protected static function time(mixed $value, string $format): ?string {
        if (!is_string($value) || $value === '' || ($ts = strtotime($value)) === false) {
            return null;
        }
        return userdate($ts, $format);
    }
}
