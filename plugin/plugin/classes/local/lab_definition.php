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
 * Lab definition gns3lab.lab/1: what the lab library stores and sends to the lab manager (MANAGER_REQUESTS П11).
 *
 * The plugin only checks the shape and the size: the lab manager knows the images and their limits and
 * checks the rest (POST /api/v1/labs/validate). Drawings and texts of a lab are not part of the definition.
 *
 * @package    mod_gns3lab
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
class lab_definition {
    /** Format name. */
    public const FORMAT = 'gns3lab.lab/1';

    /** Most nodes a Lab Slot takes. */
    public const MAX_NODES = 100;

    /** Most links: four times the nodes is more than any real lab. */
    public const MAX_LINKS = 400;

    /** Node name pattern of the Lab Slot. */
    public const NAME_PATTERN = '/^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$/';

    /** Node fields besides name, image, x and y; the lab manager uses the image default for a missing one. */
    protected const NUMBERS = ['ram_mb', 'cpus', 'adapters'];

    /**
     * An empty definition.
     *
     * @param string $title lab title
     * @return array
     */
    public static function empty(string $title): array {
        return ['format' => self::FORMAT, 'title' => $title, 'nodes' => [], 'links' => []];
    }

    /**
     * Parses a definition sent by the editor and keeps only its known fields.
     *
     * @param string $json definition as JSON
     * @param string $title lab title, set by the library and not taken from the editor
     * @return array clean definition
     * @throws \moodle_exception labdefinvalid when the shape is wrong
     */
    public static function from_json(string $json, string $title): array {
        try {
            $raw = json_decode($json, true, 16, JSON_THROW_ON_ERROR);
        } catch (\JsonException $e) {
            throw self::invalid('not JSON');
        }
        if (!is_array($raw)) {
            throw self::invalid('object expected');
        }
        $nodes = $raw['nodes'] ?? [];
        $links = $raw['links'] ?? [];
        if (!is_array($nodes) || !array_is_list($nodes) || count($nodes) > self::MAX_NODES) {
            throw self::invalid('nodes');
        }
        if (!is_array($links) || !array_is_list($links) || count($links) > self::MAX_LINKS) {
            throw self::invalid('links');
        }
        $def = self::empty(\core_text::substr(trim($title), 0, 255));
        $names = [];
        foreach ($nodes as $i => $node) {
            $name = $node['name'] ?? null;
            $image = $node['image'] ?? null;
            if (!is_string($name) || !preg_match(self::NAME_PATTERN, $name) || isset($names[$name])) {
                throw self::invalid("nodes[{$i}].name");
            }
            if (!is_string($image) || !preg_match('/^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$/', $image)) {
                throw self::invalid("nodes[{$i}].image");
            }
            $names[$name] = true;
            $clean = ['name' => $name, 'image' => $image, 'x' => self::int($node, 'x', $i), 'y' => self::int($node, 'y', $i)];
            foreach (self::NUMBERS as $key) {
                if (array_key_exists($key, $node) && $node[$key] !== null) {
                    $clean[$key] = self::int($node, $key, $i);
                }
            }
            $def['nodes'][] = $clean;
        }
        foreach ($links as $i => $link) {
            $ends = [];
            foreach (['a', 'b'] as $side) {
                $end = is_array($link) ? ($link[$side] ?? null) : null;
                if (
                    !is_array($end) || !is_string($end['node'] ?? null) || !isset($names[$end['node']])
                    || !is_int($end['adapter'] ?? null) || !is_int($end['port'] ?? null)
                    || $end['adapter'] < 0 || $end['port'] < 0 || $end['adapter'] > 255 || $end['port'] > 255
                ) {
                    throw self::invalid("links[{$i}].{$side}");
                }
                $ends[$side] = ['node' => $end['node'], 'adapter' => $end['adapter'], 'port' => $end['port']];
            }
            $def['links'][] = $ends;
        }
        return $def;
    }

    /**
     * Encodes a definition for storage and for the browser.
     *
     * @param array $def definition
     * @return string
     */
    public static function to_json(array $def): string {
        return json_encode($def, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR);
    }

    /**
     * Decodes a stored definition.
     *
     * @param string $json stored JSON
     * @return array
     */
    public static function decode(string $json): array {
        $def = json_decode($json, true);
        return is_array($def) ? $def : self::empty('');
    }

    /**
     * Builds the .gns3-like document the topology viewer shows before the lab runs.
     *
     * Node ids are the node names: they are unique in a definition. Images give the symbol, the node type
     * and the port names; a node of an unknown image is still drawn.
     *
     * @param array $def definition
     * @param \stdClass[] $images image catalog of the lab manager
     * @return \stdClass
     */
    public static function to_topology(array $def, array $images): \stdClass {
        $catalog = [];
        foreach ($images as $image) {
            $catalog[$image->id] = $image;
        }
        $nodes = [];
        foreach ($def['nodes'] ?? [] as $node) {
            $image = $catalog[$node['image']] ?? null;
            $adapters = (int) ($node['adapters'] ?? $image->adapters->default ?? 1);
            $perport = max(1, (int) ($image->ports_per_adapter ?? 1));
            $ports = [];
            for ($a = 0; $a < min($adapters, 64); $a++) {
                for ($p = 0; $p < min($perport, 64); $p++) {
                    $name = self::port_name($image, $a, $p);
                    $ports[] = ['adapter_number' => $a, 'port_number' => $p, 'name' => $name,
                        'short_name' => self::short_port_name($name)];
                }
            }
            $nodes[] = [
                'node_id' => $node['name'],
                'name' => $node['name'],
                'node_type' => (string) ($image->kind ?? ''),
                'symbol' => (string) ($image->symbol ?? ''),
                'console_type' => (string) ($image->console_type ?? 'none'),
                'x' => (int) $node['x'],
                'y' => (int) $node['y'],
                'width' => 60,
                'height' => 45,
                'ports' => $ports,
                'properties' => new \stdClass(),
            ];
        }
        $links = [];
        foreach ($def['links'] ?? [] as $i => $link) {
            $ends = [];
            foreach (['a', 'b'] as $side) {
                $ends[] = [
                    'node_id' => $link[$side]['node'],
                    'adapter_number' => (int) $link[$side]['adapter'],
                    'port_number' => (int) $link[$side]['port'],
                ];
            }
            $links[] = ['link_id' => 'link-' . $i, 'nodes' => $ends];
        }
        return (object) [
            'name' => (string) ($def['title'] ?? ''),
            'topology' => (object) ['nodes' => $nodes, 'links' => $links, 'drawings' => []],
        ];
    }

    /**
     * Port name by the image template, such as "Ethernet{adapter}/{port}".
     *
     * @param \stdClass|null $image image from the catalog
     * @param int $adapter adapter number
     * @param int $port port number
     * @return string
     */
    public static function port_name(?\stdClass $image, int $adapter, int $port): string {
        $template = is_string($image->port_name ?? null) && $image->port_name !== '' ? $image->port_name : '{adapter}/{port}';
        return str_replace(['{adapter}', '{port}'], [(string) $adapter, (string) $port], $template);
    }

    /**
     * Short port name for labels on links, the way Cisco abbreviates interfaces.
     *
     * @param string $name full port name
     * @return string
     */
    public static function short_port_name(string $name): string {
        return preg_replace(['/^GigabitEthernet/', '/^FastEthernet/', '/^Ethernet/'], ['Gi', 'Fa', 'e'], $name);
    }

    /**
     * Reads an integer field of a node.
     *
     * @param array $node node
     * @param string $key field
     * @param int $i node index, for the error
     * @return int
     */
    protected static function int(array $node, string $key, int $i): int {
        $value = $node[$key] ?? null;
        if (is_float($value) && is_finite($value)) {
            $value = (int) round($value);
        }
        if (!is_int($value) || abs($value) > 100000) {
            throw self::invalid("nodes[{$i}].{$key}");
        }
        return $value;
    }

    /**
     * The error for a definition of the wrong shape: the editor never sends one, so the place is debug info.
     *
     * @param string $where path in the definition
     * @return \moodle_exception
     */
    protected static function invalid(string $where): \moodle_exception {
        return new \moodle_exception('labdefinvalid', 'gns3lab', '', null, $where);
    }
}
