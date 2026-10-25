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
 * Facts snapshots of lab sessions, kept for a few seconds in the application cache (docs/live-state.md §6).
 *
 * Every open lab page asks for the facts every half a minute; the cache makes one manager request
 * serve them all. A fresh entry is used as is; an older one is checked with since_rev, and a 304
 * only extends it. The time of the snapshot (generated_at) moves with every collection while rev stays
 * the same (CONTRACTS §2.7), so once half of the ttl has passed the snapshot is read in full again:
 * otherwise the page could not tell a healthy collector from one that stopped.
 *
 * @package    mod_gns3lab
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
class session_facts {
    /** How long an entry is used without asking the manager, seconds. */
    public const FRESH = 15;

    /** Default ttl of a snapshot that does not name one, seconds (docs/live-state.md §2). */
    public const DEFAULT_TTL = 90;

    /**
     * The snapshot of a session, from the cache or the manager.
     *
     * @param manager_client $client manager client
     * @param string $sessionid session id
     * @return \stdClass rev (int), doc (JSON string), generatedat (int, 0 if unknown), ttl (int), fetchedat (int)
     */
    public static function get(manager_client $client, string $sessionid): \stdClass {
        $cache = self::cache();
        $key = self::key($sessionid);
        $entry = $cache->get($key);
        $now = time();
        if ($entry && $now - $entry->fetchedat < self::FRESH) {
            return $entry;
        }
        // While the snapshot time is recent, asking whether rev moved is enough.
        $conditional = $entry && $entry->generatedat > 0 && $now - $entry->generatedat < $entry->ttl / 2;
        $facts = $client->get_session_facts($sessionid, $conditional ? $entry->rev : null);
        if ($facts === null) {
            $entry->fetchedat = $now;
        } else {
            $entry = self::entry($facts, $now);
        }
        $cache->set($key, $entry);
        return $entry;
    }

    /**
     * Drops the cached snapshot of a session, so the next read asks the manager.
     *
     * @param string $sessionid session id
     */
    public static function forget(string $sessionid): void {
        self::cache()->delete(self::key($sessionid));
    }

    /**
     * Cache entry of a snapshot from the manager.
     *
     * @param \stdClass $facts snapshot checked by manager_client
     * @param int $now time of the request
     * @return \stdClass
     */
    protected static function entry(\stdClass $facts, int $now): \stdClass {
        $rev = $facts->rev ?? 0;
        if (!is_int($rev) || $rev < 0) {
            throw new manager_exception('badresponse', 200, 'facts with a bad rev');
        }
        $ttl = $facts->ttl ?? null;
        $generated = is_string($facts->generated_at ?? null) ? strtotime($facts->generated_at) : false;
        return (object) [
            'rev' => $rev,
            'doc' => json_encode($facts, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR),
            'generatedat' => $generated === false ? 0 : $generated,
            'ttl' => is_int($ttl) && $ttl > 0 ? $ttl : self::DEFAULT_TTL,
            'fetchedat' => $now,
        ];
    }

    /**
     * The cache of the snapshots.
     *
     * @return \cache
     */
    protected static function cache(): \cache {
        return \cache::make('mod_gns3lab', 'facts');
    }

    /**
     * Cache key of a session: session ids may hold characters that simple keys do not allow.
     *
     * @param string $sessionid session id
     * @return string
     */
    protected static function key(string $sessionid): string {
        $baseurl = rtrim(trim((string) get_config('gns3lab', 'managerurl')), '/');
        return 'f' . sha1($baseurl . "\n" . $sessionid);
    }
}
