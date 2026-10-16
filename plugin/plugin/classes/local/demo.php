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
 * Demo lab data bundled with the plugin, used until the lab manager is connected.
 *
 * The files in demo/ are copies of fixtures/ospf-multi-area.* from the project repository.
 *
 * @package    mod_gns3lab
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
class demo {
    /**
     * Returns the demo topology, node states and device facts.
     *
     * Objects are decoded as stdClass so that empty JSON objects stay objects when encoded again.
     *
     * @return \stdClass with topology, live and facts (ok, broken)
     */
    public static function data(): \stdClass {
        $dir = dirname(__DIR__, 2) . '/demo/';
        return (object) [
            'topology' => self::read($dir . 'ospf-multi-area.gns3'),
            'live' => self::read($dir . 'ospf-multi-area.live.json'),
            'facts' => (object) [
                'ok' => self::read($dir . 'ospf-multi-area.facts.json'),
                'broken' => self::read($dir . 'ospf-multi-area.facts-broken.json'),
            ],
        ];
    }

    /**
     * Reads and decodes one JSON file.
     *
     * @param string $file absolute path
     * @return \stdClass
     */
    private static function read(string $file): \stdClass {
        $content = file_get_contents($file);
        if ($content === false) {
            throw new \coding_exception('Missing demo file: ' . basename($file));
        }
        return json_decode($content, false, 64, JSON_THROW_ON_ERROR);
    }
}
