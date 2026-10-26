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
 * The modes of the scheme (live, seen, fog) and the mask of what a student has uncovered in the last two
 * (docs/lab-constructor/README.md §2, §12).
 *
 * The mask holds identifiers and names only: nodes, interfaces, data groups and links of the live project. It is kept
 * per student and activity. A student whose project is created anew gets new identifiers, so the old rows match nothing
 * and are removed on the next write. The mask is a teaching device, not a protection: the topology and the facts reach
 * the browser anyway.
 *
 * @package    mod_gns3lab
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
class reveal {
    /** The scheme shows the facts of the manager and what the student saw in the consoles. */
    public const LIVE = 'live';

    /** The scheme shows only the data groups the student's commands uncovered. */
    public const SEEN = 'seen';

    /** Nodes, interfaces and links are hidden until the student's actions uncover them. */
    public const FOG = 'fog';

    /** The node was opened: its console printed something. */
    public const KIND_NODE = 'node';

    /** The node was found: a cable that works leads to it. */
    public const KIND_KNOWN = 'known';

    /** An interface of a node shown in the output of a command. */
    public const KIND_IFACE = 'iface';

    /** A group of data of a node uncovered by a command. */
    public const KIND_GROUP = 'group';

    /** A cable that works at an end that is open. */
    public const KIND_LINK = 'link';

    /** Groups of data of a node (docs/lab-constructor/README.md §2.1). */
    public const GROUPS = ['l1', 'l2', 'l3', 'routes'];

    /** Most items one call takes. */
    public const MAX_PER_CALL = 500;

    /** Most rows one user has in one activity. */
    public const MAX_PER_USER = 5000;

    /** Longest interface name; the column of the table is as long. */
    public const ITEM_MAX = 100;

    /** Shape of an interface name: letters, digits and the marks of IOS and Linux names. */
    public const IFACE_PATTERN = '~^[A-Za-z0-9][A-Za-z0-9._:/@+-]*$~';

    /**
     * Modes in the order of the activity form.
     *
     * @return string[]
     */
    public static function modes(): array {
        return [self::LIVE, self::SEEN, self::FOG];
    }

    /**
     * The mode of an activity; a value nobody knows counts as live.
     *
     * @param \stdClass $instance gns3lab record
     * @return string
     */
    public static function mode(\stdClass $instance): string {
        $mode = (string) ($instance->factsview ?? self::LIVE);
        return in_array($mode, self::modes(), true) ? $mode : self::LIVE;
    }

    /**
     * Node names from the setting: a JSON list or names separated by commas or line breaks.
     *
     * @param string|null $raw the setting
     * @return string[] names without repeats, in the order given
     */
    public static function parse_names(?string $raw): array {
        $raw = trim((string) $raw);
        if ($raw === '') {
            return [];
        }
        $list = json_decode($raw, true);
        if (!is_array($list)) {
            $list = preg_split('/[,\n\r]+/', $raw);
        }
        $names = [];
        foreach ($list as $name) {
            $name = is_string($name) ? trim($name) : '';
            if ($name !== '' && !in_array($name, $names, true)) {
                $names[] = $name;
            }
        }
        return $names;
    }

    /**
     * The setting as it is stored: a JSON list, empty for no names.
     *
     * @param string[] $names node names
     * @return string
     */
    public static function encode_names(array $names): string {
        $names = array_values(array_unique(array_filter(array_map('trim', $names), fn($n) => $n !== '')));
        return $names ? json_encode($names, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR) : '';
    }

    /**
     * Names of the nodes a fog starts from; empty when every node starts as a silhouette.
     *
     * @param \stdClass $instance gns3lab record
     * @return string[]
     */
    public static function start_names(\stdClass $instance): array {
        return self::mode($instance) === self::FOG ? self::parse_names($instance->fogstart ?? '') : [];
    }

    /**
     * Names of the nodes of the lab an activity runs, for the form.
     *
     * @param \stdClass|array $lab a library lab definition or the .gns3 topology of a reference lab
     * @return string[]|null null if the lab has no node list to read
     */
    public static function node_names(\stdClass|array $lab): ?array {
        $names = [];
        if (is_array($lab)) {
            foreach ($lab['nodes'] ?? [] as $node) {
                $names[] = (string) ($node['name'] ?? '');
            }
        } else {
            foreach ($lab->topology->nodes ?? [] as $node) {
                $names[] = (string) ($node->name ?? '');
            }
        }
        $names = array_values(array_unique(array_filter($names, fn($n) => $n !== '')));
        return $names ?: null;
    }

    /**
     * Names of the nodes of the lab an activity runs, for the form of the activity.
     *
     * A lab of the library is read from its definition, a reference lab from its preview on the manager.
     *
     * @param \stdClass $lab labversionid and labref of an activity or of its form
     * @return string[]|null null if the lab is not known or the manager does not answer
     */
    public static function lab_node_names(\stdClass $lab): ?array {
        $source = lab_library::activity_lab($lab);
        if (is_array($source)) {
            return self::node_names($source);
        }
        if ($source === null || !manager_client::is_configured()) {
            return null;
        }
        try {
            return self::node_names(\core\di::get(manager_client::class)->get_lab_topology($source));
        } catch (manager_exception $e) {
            debugging('mod_gns3lab: no preview from the lab manager: ' . $e->debuginfo, DEBUG_DEVELOPER);
            return null;
        }
    }

    /**
     * Nodes and links of the live topology of a session.
     *
     * @param \stdClass $session session from manager_client
     * @return array{nodes: array<string, string>, links: array<string, true>} node id => name, link ids
     */
    public static function live_ids(\stdClass $session): array {
        $nodes = [];
        $links = [];
        foreach ($session->topology->topology->nodes ?? [] as $node) {
            if (isset($node->node_id) && is_string($node->node_id)) {
                $nodes[$node->node_id] = (string) ($node->name ?? '');
            }
        }
        foreach ($session->topology->topology->links ?? [] as $link) {
            if (isset($link->link_id) && is_string($link->link_id)) {
                $links[$link->link_id] = true;
            }
        }
        return ['nodes' => $nodes, 'links' => $links];
    }

    /**
     * The mask of a user in an activity.
     *
     * @param int $labid gns3lab id
     * @param int $userid owner of the lab
     * @return array[] each: nodeid, kind, item
     */
    public static function load(int $labid, int $userid): array {
        global $DB;

        $rows = $DB->get_records(
            'gns3lab_reveal',
            ['gns3labid' => $labid, 'userid' => $userid],
            'id',
            'id, nodeid, kind, item'
        );
        return array_map(
            fn($r) => ['nodeid' => (string) $r->nodeid, 'kind' => (string) $r->kind, 'item' => (string) $r->item],
            array_values($rows)
        );
    }

    /**
     * Adds items to the mask. Nothing is ever removed except what belongs to an earlier project of the lab.
     *
     * @param \stdClass $instance gns3lab record
     * @param int $userid owner of the lab
     * @param \stdClass $session running session of the owner
     * @param array[] $items each: nodeid, kind, item
     * @return int rows added
     * @throws \invalid_parameter_exception for an item that is not in the live topology or is not well formed
     * @throws \moodle_exception revealfull when the user has too many rows
     */
    public static function add(\stdClass $instance, int $userid, \stdClass $session, array $items): int {
        global $DB;

        if (count($items) > self::MAX_PER_CALL) {
            throw new \invalid_parameter_exception('too many items');
        }
        $ids = self::live_ids($session);
        $wanted = [];
        foreach ($items as $item) {
            $row = self::checked($item, $ids);
            $wanted[implode("\n", $row)] = $row;
        }

        $labid = (int) $instance->id;
        self::prune($labid, $userid, $ids);
        $have = [];
        foreach (self::load($labid, $userid) as $row) {
            $have[implode("\n", [$row['nodeid'], $row['kind'], $row['item']])] = true;
        }
        $added = 0;
        $now = time();
        foreach ($wanted as $key => [$nodeid, $kind, $item]) {
            if (isset($have[$key])) {
                continue;
            }
            if (count($have) + $added >= self::MAX_PER_USER) {
                throw new \moodle_exception('revealfull', 'gns3lab');
            }
            try {
                $DB->insert_record('gns3lab_reveal', (object) [
                    'gns3labid' => $labid, 'userid' => $userid, 'nodeid' => $nodeid, 'kind' => $kind, 'item' => $item,
                    'timecreated' => $now,
                ], false);
                $added++;
            } catch (\dml_write_exception $e) {
                // The same item arrived from another tab a moment earlier: the unique index has it already.
                debugging('mod_gns3lab: reveal item already stored: ' . $e->getMessage(), DEBUG_DEVELOPER);
            }
        }
        return $added;
    }

    /**
     * One item of a call, checked against the live topology.
     *
     * @param mixed $item nodeid, kind, item
     * @param array $ids result of live_ids()
     * @return string[] nodeid, kind, item
     */
    protected static function checked(mixed $item, array $ids): array {
        $nodeid = (string) ($item['nodeid'] ?? '');
        $kind = (string) ($item['kind'] ?? '');
        $value = (string) ($item['item'] ?? '');
        if ($kind === self::KIND_LINK) {
            if ($nodeid !== '' || !isset($ids['links'][$value])) {
                throw new \invalid_parameter_exception('link not in the topology');
            }
            return ['', $kind, $value];
        }
        if (!isset($ids['nodes'][$nodeid])) {
            throw new \invalid_parameter_exception('node not in the topology');
        }
        $valid = match ($kind) {
            self::KIND_NODE, self::KIND_KNOWN => $value === '',
            self::KIND_IFACE => strlen($value) <= self::ITEM_MAX && preg_match(self::IFACE_PATTERN, $value) === 1,
            self::KIND_GROUP => in_array($value, self::GROUPS, true),
            default => false,
        };
        if (!$valid) {
            throw new \invalid_parameter_exception('bad reveal item');
        }
        return [$nodeid, $kind, $value];
    }

    /**
     * Removes the rows of an earlier project of the lab: nodes and links the live topology does not have.
     *
     * @param int $labid gns3lab id
     * @param int $userid owner of the lab
     * @param array $ids result of live_ids()
     */
    protected static function prune(int $labid, int $userid, array $ids): void {
        global $DB;

        $stale = [];
        $rows = $DB->get_records('gns3lab_reveal', ['gns3labid' => $labid, 'userid' => $userid], '', 'id, nodeid, kind, item');
        foreach ($rows as $r) {
            $gone = $r->kind === self::KIND_LINK ? !isset($ids['links'][$r->item]) : !isset($ids['nodes'][$r->nodeid]);
            if ($gone) {
                $stale[] = $r->id;
            }
        }
        if ($stale) {
            $DB->delete_records_list('gns3lab_reveal', 'id', $stale);
        }
    }

    /**
     * Whether a student may open the console of a node.
     *
     * Only a fog with starting nodes closes consoles: a node that is neither a starting one nor found or opened in the
     * mask is not there for the student yet. When no starting name matches a node of the lab the setting is ignored:
     * the lab was changed after the setting was made, and nobody could open anything.
     *
     * @param \stdClass $instance gns3lab record
     * @param int $userid owner of the lab
     * @param \stdClass $session running session of the owner
     * @param string $nodeid node of the live project
     * @return bool
     */
    public static function console_allowed(\stdClass $instance, int $userid, \stdClass $session, string $nodeid): bool {
        global $DB;

        $start = self::start_names($instance);
        if (!$start) {
            return true;
        }
        $names = self::live_ids($session)['nodes'];
        if (!array_intersect($start, $names)) {
            return true;
        }
        if (in_array($names[$nodeid] ?? '', $start, true)) {
            return true;
        }
        [$insql, $params] = $DB->get_in_or_equal([self::KIND_NODE, self::KIND_KNOWN], SQL_PARAMS_NAMED);
        return $DB->record_exists_select(
            'gns3lab_reveal',
            "gns3labid = :labid AND userid = :userid AND nodeid = :nodeid AND kind $insql",
            ['labid' => $instance->id, 'userid' => $userid, 'nodeid' => $nodeid] + $params
        );
    }

    /**
     * Deletes the masks of an activity (all users) or of one user in it.
     *
     * @param int $labid gns3lab id
     * @param int[]|null $userids users, null for all
     */
    public static function delete(int $labid, ?array $userids = null): void {
        global $DB;

        if ($userids === null) {
            $DB->delete_records('gns3lab_reveal', ['gns3labid' => $labid]);
            return;
        }
        if (!$userids) {
            return;
        }
        [$insql, $params] = $DB->get_in_or_equal($userids, SQL_PARAMS_NAMED);
        $DB->delete_records_select('gns3lab_reveal', "gns3labid = :labid AND userid $insql", ['labid' => $labid] + $params);
    }
}
