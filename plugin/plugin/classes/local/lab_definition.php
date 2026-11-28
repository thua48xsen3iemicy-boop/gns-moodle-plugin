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
 * Lab definition gns3lab.lab/1 or gns3lab.lab/2: what the lab library stores and sends to the lab manager
 * (MANAGER_REQUESTS П11, lab constructor stage 2).
 *
 * lab/2 is lab/1 plus segments (docs/lab-constructor/README.md §3), the checks document and the teacher's setup steps
 * (`setup`, docs/lab-setup/README.md §4). A definition with any of them is lab/2 (with empty segments if it has none),
 * one without them is lab/1, whatever format the editor named.
 *
 * The plugin only checks the shape and the size: the lab manager knows the images and their limits and
 * checks the rest (POST /api/v1/labs/validate). The texts and boxes a teacher puts on the scheme (ANNOTATIONS) belong to
 * the plugin alone: for_manager() leaves them out and to_topology() draws them as the drawings of the .gns3 document.
 *
 * @package    mod_gns3lab
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
class lab_definition {
    /** Format name. */
    public const FORMAT = 'gns3lab.lab/1';

    /** Format with segments (lab constructor). */
    public const FORMAT_2 = 'gns3lab.lab/2';

    /** Format of the checks document of a lab/2 (CONTRACTS §2.9, docs/autochecks/README.md §4). */
    public const CHECKS_FORMAT = 'gns3lab.checks/1';

    /** Who sets up a segment (CONTRACTS §2.9): the lab manager (the default) or the student. */
    public const CONFIGURE = ['manager', 'student'];

    /** Longest checks document as JSON: far more than any real lab needs. */
    public const CHECKS_MAX = 65536;

    /** Longest list of setup steps as JSON (docs/lab-setup/README.md §4); the lab manager checks the steps themselves. */
    public const SETUP_MAX = 65536;

    /** Most segments in a lab, as in the lab manager. */
    public const MAX_SEGMENTS = 100;

    /** Most devices in a segment, as in the lab manager. */
    public const MAX_MEMBERS = 100;

    /** Segment name pattern of the lab manager. */
    public const SEGMENT_PATTERN = '/^[A-Za-z][A-Za-z0-9_]{0,31}$/';

    /** Most nodes a Lab Slot takes. */
    public const MAX_NODES = 100;

    /** @var int additional disks of a node: GNS3 has hdb, hdc and hdd */
    public const MAX_DISKS = 3;

    /** Most links: four times the nodes is more than any real lab. */
    public const MAX_LINKS = 400;

    /** Node name pattern of the Lab Slot (NodeSpec.name). */
    public const NAME_PATTERN = '/^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$/';

    /** Longest title the lab manager takes. */
    public const TITLE_MAX = 200;

    /**
     * Node field of the plugin alone: students may not paste the clipboard into the console of the node.
     *
     * The lab manager refuses unknown fields (CONTRACTS 2.6), so for_manager() takes it out of whatever goes there.
     */
    public const NOPASTE = 'nopaste';

    /**
     * Node field of the plugin alone: the student gets no console of the node at all and works with it over the network
     * (SSH from a workstation node). Teachers who may observe and the author of the lab keep the console.
     *
     * Unlike NOPASTE this holds on the server: mod_gns3lab_get_console_ticket refuses such a node to its owner. Like
     * NOPASTE, for_manager() takes it out.
     */
    public const NOCONSOLE = 'noconsole';

    /**
     * Node field the lab manager takes (CONTRACTS 2.6, 6.4): additional console lines ttyS1..ttyS3 of the node, each with
     * its own login, so that a student keeps several independent terminals on it. 0 is no field.
     */
    public const EXTRA_CONSOLES = 'extra_consoles';

    /** Most additional console lines of a node (manager CONTRACTS 6.4). */
    public const MAX_EXTRA_CONSOLES = 3;

    /**
     * Node field of the plugin alone: the icon the lab pages draw instead of the one of the image category.
     *
     * Like NOPASTE, for_manager() takes it out; the device stays what its image is (console snippets, hosts).
     */
    public const ICON = 'icon';

    /** Icons a node may take: the symbols of templates/icons.mustache, without unknown. */
    public const ICONS = ['router', 'switch', 'pc', 'server', 'firewall', 'cloud', 'hypervisor'];

    /**
     * Top level field of the plugin alone: texts and boxes with captions on the scheme of the lab.
     *
     * Each is {kind: text|box, x, y, text, size, color?, bold?, italic?} and a box also has w, h and fill. The lab
     * manager never sees them (for_manager()); the lab pages get them as .gns3 drawings (to_topology()).
     */
    public const ANNOTATIONS = 'annotations';

    /** Most texts and boxes on one scheme. */
    public const MAX_ANNOTATIONS = 200;

    /** Longest text of one annotation, in characters. */
    public const ANNOTATION_TEXT_MAX = 500;

    /** Font sizes an annotation may take. */
    public const ANNOTATION_SIZE = [8, 72];

    /** Font size of an annotation that names none. */
    public const ANNOTATION_SIZE_DEFAULT = 14;

    /** Box colour when the editor names none. */
    public const ANNOTATION_FILL_DEFAULT = '#1e88e5';

    /** Sizes a box may take, in scene units. */
    public const ANNOTATION_BOX = [20, 5000];

    /** Node fields besides name, image, x and y; the lab manager uses the image default for a missing one. */
    protected const NUMBERS = ['ram_mb', 'cpus', 'adapters', 'uplink_port'];

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
     * The definition as the lab manager takes it: without the fields that only the plugin knows.
     *
     * @param array $def clean definition
     * @return array
     */
    public static function for_manager(array $def): array {
        unset($def[self::ANNOTATIONS]);
        foreach ($def['nodes'] ?? [] as $i => $node) {
            if (is_array($node)) {
                unset($def['nodes'][$i][self::NOPASTE], $def['nodes'][$i][self::NOCONSOLE], $def['nodes'][$i][self::ICON]);
            }
        }
        return $def;
    }

    /**
     * Lets the clipboard into every console of a lab page: takes the mark of to_topology() off the nodes.
     *
     * @param \stdClass $doc lab as to_topology() gives it (a .gns3 document: topology.nodes), changed
     */
    public static function allow_paste(\stdClass $doc): void {
        foreach ($doc->topology->nodes ?? [] as $i => $node) {
            if (is_array($node)) {
                unset($doc->topology->nodes[$i][self::NOPASTE], $doc->topology->nodes[$i][self::NOCONSOLE]);
            } else if (is_object($node)) {
                unset($node->{self::NOPASTE}, $node->{self::NOCONSOLE});
            }
        }
    }

    /**
     * Names of the nodes whose console the student does not get (NOCONSOLE).
     *
     * @param array $def clean definition
     * @return string[]
     */
    public static function closed_names(array $def): array {
        $names = [];
        foreach ($def['nodes'] ?? [] as $node) {
            if (is_array($node) && !empty($node[self::NOCONSOLE]) && is_string($node['name'] ?? null)) {
                $names[] = $node['name'];
            }
        }
        return $names;
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
        $def = self::empty(self::title($title));
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
            // Random guest interface names (manager CONTRACTS 2.6): only true is kept, like the lab manager does.
            if (array_key_exists('random_ifnames', $node) && $node['random_ifnames'] !== null) {
                if (!is_bool($node['random_ifnames'])) {
                    throw self::invalid("nodes[{$i}].random_ifnames");
                }
                if ($node['random_ifnames']) {
                    $clean['random_ifnames'] = true;
                }
            }
            // No pasting into the console of the node: like random_ifnames, only true is kept.
            if (array_key_exists(self::NOPASTE, $node) && $node[self::NOPASTE] !== null) {
                if (!is_bool($node[self::NOPASTE])) {
                    throw self::invalid("nodes[{$i}]." . self::NOPASTE);
                }
                if ($node[self::NOPASTE]) {
                    $clean[self::NOPASTE] = true;
                }
            }
            // No console for the student at all: like NOPASTE, only true is kept.
            if (array_key_exists(self::NOCONSOLE, $node) && $node[self::NOCONSOLE] !== null) {
                if (!is_bool($node[self::NOCONSOLE])) {
                    throw self::invalid("nodes[{$i}]." . self::NOCONSOLE);
                }
                if ($node[self::NOCONSOLE]) {
                    $clean[self::NOCONSOLE] = true;
                }
            }
            // Additional console lines (manager CONTRACTS 2.6): 0 is no field, like the lab manager does.
            if (array_key_exists(self::EXTRA_CONSOLES, $node) && $node[self::EXTRA_CONSOLES] !== null) {
                $lines = $node[self::EXTRA_CONSOLES];
                if (!is_int($lines) || $lines < 0 || $lines > self::MAX_EXTRA_CONSOLES) {
                    throw self::invalid("nodes[{$i}]." . self::EXTRA_CONSOLES);
                }
                if ($lines > 0) {
                    $clean[self::EXTRA_CONSOLES] = $lines;
                }
            }
            // Icon of the node: one of ICONS, an empty one is no field (the icon of the image category).
            if (array_key_exists(self::ICON, $node) && $node[self::ICON] !== null && $node[self::ICON] !== '') {
                if (!in_array($node[self::ICON], self::ICONS, true)) {
                    throw self::invalid("nodes[{$i}]." . self::ICON);
                }
                $clean[self::ICON] = $node[self::ICON];
            }
            // Additional blank disks (manager CONTRACTS 2.6): an empty list is no field, like the lab manager does.
            if (array_key_exists('disks', $node) && $node['disks'] !== null) {
                $disks = $node['disks'];
                if (!is_array($disks) || !array_is_list($disks) || count($disks) > self::MAX_DISKS) {
                    throw self::invalid("nodes[{$i}].disks");
                }
                foreach ($disks as $j => $disk) {
                    $size = is_array($disk) ? ($disk['size_gb'] ?? null) : null;
                    if (!is_int($size) || $size < 1 || $size > 1024) {
                        throw self::invalid("nodes[{$i}].disks[{$j}]");
                    }
                    $clean['disks'][] = ['size_gb' => $size];
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
        $segments = self::segments($raw['segments'] ?? [], $names);
        if ($segments) {
            $def['format'] = self::FORMAT_2;
            $def['segments'] = $segments;
        }
        if (isset($raw['checks'])) {
            // The content is the lab manager's to check (POST /labs/validate); here only the shape and the size.
            $checks = $raw['checks'];
            if (
                !is_array($checks) || array_is_list($checks) || ($checks['format'] ?? null) !== self::CHECKS_FORMAT
                || strlen(json_encode($checks)) > self::CHECKS_MAX
            ) {
                throw self::invalid('checks');
            }
            $def['format'] = self::FORMAT_2;
            $def['segments'] = $segments;
            $def['checks'] = self::checks_objects($checks);
        }
        if (isset($raw['setup']) && $raw['setup'] !== []) {
            // The teacher's setup steps (manager CONTRACTS §2.6 `setup`): a list of objects; the content is the manager's.
            $setup = $raw['setup'];
            if (
                !is_array($setup) || !array_is_list($setup) || strlen(json_encode($setup)) > self::SETUP_MAX
                || array_filter($setup, fn($step) => !is_array($step) || ($step !== [] && array_is_list($step)))
            ) {
                throw self::invalid('setup');
            }
            $def['format'] = self::FORMAT_2;
            $def['segments'] = $segments;
            $def['setup'] = self::setup_objects($setup);
        }
        $annotations = self::annotations($raw[self::ANNOTATIONS] ?? []);
        if ($annotations) {
            $def[self::ANNOTATIONS] = $annotations;
        }
        return $def;
    }

    /**
     * Keeps the known fields of the texts and boxes on the scheme and refuses a wrong shape.
     *
     * A text without any letters is dropped: the editor leaves one behind when the teacher clears it.
     *
     * @param mixed $raw annotations as sent by the editor
     * @return array clean annotations
     * @throws \moodle_exception labdefinvalid when the shape is wrong
     */
    protected static function annotations(mixed $raw): array {
        if (!is_array($raw) || !array_is_list($raw) || count($raw) > self::MAX_ANNOTATIONS) {
            throw self::invalid(self::ANNOTATIONS);
        }
        $out = [];
        foreach ($raw as $i => $item) {
            $where = self::ANNOTATIONS . "[{$i}]";
            $kind = is_array($item) ? ($item['kind'] ?? null) : null;
            if ($kind !== 'text' && $kind !== 'box') {
                throw self::invalid("{$where}.kind");
            }
            $text = $item['text'] ?? '';
            if (!is_string($text) || \core_text::strlen($text) > self::ANNOTATION_TEXT_MAX) {
                throw self::invalid("{$where}.text");
            }
            $text = rtrim(str_replace(["\r\n", "\r"], "\n", $text));
            if ($kind === 'text' && trim($text) === '') {
                continue;
            }
            $clean = ['kind' => $kind, 'x' => self::number($item, 'x', $where, -100000, 100000),
                'y' => self::number($item, 'y', $where, -100000, 100000)];
            if ($kind === 'box') {
                [$min, $max] = self::ANNOTATION_BOX;
                $clean['w'] = self::number($item, 'w', $where, $min, $max);
                $clean['h'] = self::number($item, 'h', $where, $min, $max);
            }
            $clean['text'] = $text;
            [$min, $max] = self::ANNOTATION_SIZE;
            $clean['size'] = array_key_exists('size', $item) && $item['size'] !== null
                ? self::number($item, 'size', $where, $min, $max) : self::ANNOTATION_SIZE_DEFAULT;
            foreach (['color', 'fill'] as $key) {
                $value = $item[$key] ?? null;
                if ($value === null || $value === '' || ($key === 'fill' && $kind !== 'box')) {
                    continue;
                }
                if (!is_string($value) || !preg_match('/^#[0-9a-fA-F]{6}$/', $value)) {
                    throw self::invalid("{$where}.{$key}");
                }
                $clean[$key] = strtolower($value);
            }
            if ($kind === 'box' && !isset($clean['fill'])) {
                $clean['fill'] = self::ANNOTATION_FILL_DEFAULT;
            }
            foreach (['bold', 'italic'] as $key) {
                if (array_key_exists($key, $item) && $item[$key] !== null) {
                    if (!is_bool($item[$key])) {
                        throw self::invalid("{$where}.{$key}");
                    }
                    if ($item[$key]) {
                        $clean[$key] = true;
                    }
                }
            }
            $out[] = $clean;
        }
        return $out;
    }

    /**
     * The .gns3 drawings of the texts and boxes of a definition, as the lab pages draw them (stage.js sanitizeDrawing).
     *
     * A box lies under the texts. Its caption sits in its top left corner, in the colour of the box unless it has one.
     * A text without a colour takes the text colour of the page theme. Lines go one under the other, 1.25 font sizes
     * apart: editor.js draws the same.
     *
     * @param array $def definition
     * @return array drawings: drawing_id, x, y, z, rotation, svg
     */
    public static function drawings(array $def): array {
        $out = [];
        foreach ($def[self::ANNOTATIONS] ?? [] as $i => $a) {
            if (!is_array($a) || !in_array($a['kind'] ?? '', ['text', 'box'], true)) {
                continue;
            }
            $size = (int) ($a['size'] ?? self::ANNOTATION_SIZE_DEFAULT);
            $isbox = $a['kind'] === 'box';
            $lines = explode("\n", (string) ($a['text'] ?? ''));
            $step = $size * 1.25;
            $pad = $isbox ? 8 : 0;
            $color = $a['color'] ?? ($isbox ? ($a['fill'] ?? self::ANNOTATION_FILL_DEFAULT) : null);
            $attrs = ' font-size="' . $size . '"' . (!empty($a['bold']) ? ' font-weight="bold"' : '')
                . (!empty($a['italic']) ? ' font-style="italic"' : '') . ($color ? ' fill="' . s($color) . '"' : '');
            $body = '';
            $widest = 0;
            foreach ($lines as $k => $line) {
                $widest = max($widest, \core_text::strlen($line));
                if (trim($line) === '') {
                    continue;
                }
                $y = round($pad + $size + $k * $step, 2);
                $body .= '<text x="' . $pad . '" y="' . $y . '"' . $attrs . '>'
                    . htmlspecialchars($line, ENT_XML1 | ENT_QUOTES, 'UTF-8') . '</text>';
            }
            if ($isbox) {
                $w = (int) $a['w'];
                $h = (int) $a['h'];
                $fill = s($a['fill'] ?? self::ANNOTATION_FILL_DEFAULT);
                $body = '<rect x="0" y="0" width="' . $w . '" height="' . $h . '" rx="8" fill="' . $fill
                    . '" fill-opacity="0.12" stroke="' . $fill . '" stroke-width="2"/>' . $body;
            } else {
                $w = (int) ceil($widest * $size * 0.6);
                $h = (int) ceil(count($lines) * $step);
            }
            $out[] = [
                'drawing_id' => 'note-' . $i,
                'x' => (int) $a['x'],
                'y' => (int) $a['y'],
                'z' => $isbox ? 0 : 1,
                'rotation' => 0,
                'svg' => '<svg xmlns="http://www.w3.org/2000/svg" width="' . $w . '" height="' . $h . '">' . $body . '</svg>',
            ];
        }
        return $out;
    }

    /**
     * Keeps the known fields of the segments and refuses a wrong shape; values are the lab manager's to check.
     *
     * @param mixed $raw segments as sent by the editor
     * @param array $names node names of the lab as keys
     * @return array clean segments
     * @throws \moodle_exception labdefinvalid when the shape is wrong
     */
    protected static function segments(mixed $raw, array $names): array {
        if (!is_array($raw) || !array_is_list($raw) || count($raw) > self::MAX_SEGMENTS) {
            throw self::invalid('segments');
        }
        $out = [];
        foreach ($raw as $i => $segment) {
            $name = is_array($segment) ? ($segment['name'] ?? null) : null;
            if (!is_string($name) || !preg_match(self::SEGMENT_PATTERN, $name)) {
                throw self::invalid("segments[{$i}].name");
            }
            $subnet = $segment['subnet']['fixed'] ?? null;
            if (!is_string($subnet) || strlen($subnet) > 32) {
                throw self::invalid("segments[{$i}].subnet");
            }
            $clean = ['name' => $name];
            if (isset($segment['vlan'])) {
                $vlan = is_array($segment['vlan']) ? ($segment['vlan']['fixed'] ?? null) : null;
                if (!is_int($vlan)) {
                    throw self::invalid("segments[{$i}].vlan");
                }
                $clean['vlan'] = ['fixed' => $vlan];
            }
            $clean['subnet'] = ['fixed' => $subnet];
            if (isset($segment['gateway'])) {
                $clean['gateway'] = self::port($segment['gateway'], $names, "segments[{$i}].gateway");
            }
            if (isset($segment['configure'])) {
                if (!in_array($segment['configure'], self::CONFIGURE, true)) {
                    throw self::invalid("segments[{$i}].configure");
                }
                if ($segment['configure'] === 'student') {
                    $clean['configure'] = 'student';
                }
            }
            $members = $segment['members'] ?? [];
            if (!is_array($members) || !array_is_list($members) || count($members) > self::MAX_MEMBERS) {
                throw self::invalid("segments[{$i}].members");
            }
            $clean['members'] = [];
            foreach ($members as $j => $member) {
                $clean['members'][] = self::port($member, $names, "segments[{$i}].members[{$j}]");
            }
            $out[] = $clean;
        }
        return $out;
    }

    /**
     * A port of a segment: node, adapter, port and an optional address number.
     *
     * @param mixed $raw port as sent by the editor
     * @param array $names node names of the lab as keys
     * @param string $where path, for the error
     * @return array
     */
    protected static function port(mixed $raw, array $names, string $where): array {
        if (
            !is_array($raw) || !is_string($raw['node'] ?? null) || !isset($names[$raw['node']])
            || !is_int($raw['adapter'] ?? null) || !is_int($raw['port'] ?? null)
            || $raw['adapter'] < 0 || $raw['port'] < 0 || $raw['adapter'] > 255 || $raw['port'] > 255
        ) {
            throw self::invalid($where);
        }
        $port = ['node' => $raw['node'], 'adapter' => $raw['adapter'], 'port' => $raw['port']];
        if (isset($raw['host'])) {
            if (!is_int($raw['host']) || $raw['host'] < 1 || $raw['host'] > 65535) {
                throw self::invalid($where . '.host');
            }
            $port['host'] = $raw['host'];
        }
        return $port;
    }

    /**
     * The lab title as the lab manager takes it: the lab name, cut to TITLE_MAX characters.
     *
     * The title is not part of lab_def, so a renamed lab keeps its hash and the students' work.
     *
     * @param string $name lab name
     * @return string
     */
    public static function title(string $name): string {
        return \core_text::substr(trim($name), 0, self::TITLE_MAX);
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
        if (is_array($def) && is_array($def['checks'] ?? null)) {
            $def['checks'] = self::checks_objects($def['checks']);
        }
        if (is_array($def) && is_array($def['setup'] ?? null)) {
            $def['setup'] = self::setup_objects($def['setup']);
        }
        return is_array($def) ? $def : self::empty('');
    }

    /**
     * The definition without its checks document; a lab/2 that had nothing but the checks becomes lab/1 again.
     *
     * The setup steps stay: they are part of the lab (docs/lab-setup/README.md §1), not of the activities.
     *
     * @param array $def clean definition
     * @return array
     */
    public static function without_checks(array $def): array {
        if (!array_key_exists('checks', $def)) {
            return $def;
        }
        unset($def['checks']);
        if (empty($def['segments']) && empty($def['setup'])) {
            unset($def['segments']);
            $def['format'] = self::FORMAT;
        }
        return $def;
    }

    /**
     * Turns empty setup steps back into objects: the lab manager answers `required` for them, not `bad_type`.
     *
     * @param array $setup setup steps decoded into arrays
     * @return array
     */
    public static function setup_objects(array $setup): array {
        foreach ($setup as $i => $step) {
            if ($step === []) {
                $setup[$i] = new \stdClass();
            }
        }
        return $setup;
    }

    /**
     * Turns the empty objects of a checks document back into objects.
     *
     * The definition is decoded into arrays, and an empty JSON object would go to the lab manager as an empty list,
     * which it refuses (bad_type). The objects of gns3lab.checks/1 are known: the document itself, nodes and each node
     * in it, addresses, groups, switches, rules, each script and its expect.
     *
     * @param array $checks checks document decoded into arrays
     * @return array
     */
    public static function checks_objects(array $checks): array {
        foreach (['nodes', 'addresses', 'groups', 'switches', 'rules'] as $key) {
            if (isset($checks[$key]) && $checks[$key] === []) {
                $checks[$key] = new \stdClass();
            }
        }
        if (is_array($checks['nodes'] ?? null)) {
            foreach ($checks['nodes'] as $name => $node) {
                if ($node === []) {
                    $checks['nodes'][$name] = new \stdClass();
                }
            }
        }
        if (is_array($checks['scripts'] ?? null)) {
            foreach ($checks['scripts'] as $i => $script) {
                // A script and its expect are objects too (the teacher's scripts, CONTRACTS §2.9).
                if ($script === []) {
                    $checks['scripts'][$i] = new \stdClass();
                } else if (is_array($script) && ($script['expect'] ?? null) === []) {
                    $checks['scripts'][$i]['expect'] = new \stdClass();
                }
            }
        }
        return $checks;
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
            $uplink = $node['uplink_port'] ?? $image->uplink_port ?? null;
            for ($a = 0; $a < min($adapters, 64); $a++) {
                if ($a === $uplink) {
                    continue;
                }
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
            if (!empty($node[self::NOPASTE])) {
                // The lab page keeps the clipboard out of the console of this node (consoles.js).
                $nodes[count($nodes) - 1][self::NOPASTE] = true;
            }
            if (!empty($node[self::NOCONSOLE])) {
                // The lab page offers no console of this node; mod_gns3lab_get_console_ticket refuses it too.
                $nodes[count($nodes) - 1][self::NOCONSOLE] = true;
            }
            if ($uplink !== null) {
                // The node with the external port is the provider of the lab (ISP): the reach of the scheme does not
                // ask it for a way back to the networks of its client (facts.js reach).
                $nodes[count($nodes) - 1]['uplink'] = true;
            }
            if (!empty($node[self::ICON])) {
                // The lab pages draw this icon, the category of the node still comes from its image (model.js).
                $nodes[count($nodes) - 1][self::ICON] = $node[self::ICON];
            }
            if (($image->guest ?? '') === 'routeros') {
                // MikroTik RouterOS: the console snippets of the node are those of RouterOS (snippets.js kindOf).
                $nodes[count($nodes) - 1]['guest'] = 'routeros';
            }
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
            'topology' => (object) ['nodes' => $nodes, 'links' => $links, 'drawings' => self::drawings($def)],
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
     * Reads an integer field of an annotation.
     *
     * @param array $item annotation
     * @param string $key field
     * @param string $where path of the annotation, for the error
     * @param int $min smallest value
     * @param int $max largest value
     * @return int
     */
    protected static function number(array $item, string $key, string $where, int $min, int $max): int {
        $value = $item[$key] ?? null;
        if (is_float($value) && is_finite($value)) {
            $value = (int) round($value);
        }
        if (!is_int($value) || $value < $min || $value > $max) {
            throw self::invalid("{$where}.{$key}");
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
