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
 * The lab an activity's auto-checks are written for (docs/autochecks.md §12): its devices for the checks block of the
 * activity form and the prompt, and the lab manager's check of a document against it.
 *
 * The checks of an activity name the nodes of its lab (scripts, groups, nodes): a document is always checked against
 * the published version (or the reference lab) chosen in the form, never stored with it.
 *
 * @package    mod_gns3lab
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
class checks_lab {
    /**
     * What an activity with these settings runs: a published library version or a reference lab.
     *
     * @param int $versionid published version, 0 for none
     * @param string $labref reference lab on the lab manager
     * @return string|array|null lab_ref, lab definition, or null
     */
    public static function lab_of(int $versionid, string $labref): string|array|null {
        return lab_library::activity_lab((object) ['labversionid' => $versionid, 'labref' => $labref]);
    }

    /**
     * The devices of a lab as the checks block and the prompt know them.
     *
     * A node takes scripts when it is QEMU and not the external connection of the lab (the lab manager checks it
     * again). A reference lab is read from the lab manager; the checks it carries itself (meta.json) stay there.
     *
     * @param string|array|null $lab what lab_of() gives
     * @return array found (bool), catalog (bool: a reference lab), nodes [{name, image, kind, category, scripts}],
     *     links [{a, b}] (ends as "node port"), segments (list), labchecks (gns3lab.checks/1 of the lab or null)
     */
    public static function context(string|array|null $lab): array {
        $out = ['found' => false, 'catalog' => is_string($lab), 'nodes' => [], 'links' => [], 'segments' => [],
            'labchecks' => checks::lab_document($lab)];
        if (is_array($lab)) {
            $images = [];
            try {
                foreach (\core\di::get(manager_client::class)->get_images() as $image) {
                    $images[$image->id] = $image;
                }
            } catch (manager_exception $e) {
                // Without the catalog the devices are still known by name; the manager checks the rest.
                $images = [];
            }
            $byname = [];
            foreach ($lab['nodes'] ?? [] as $node) {
                $image = $images[$node['image'] ?? ''] ?? null;
                $byname[$node['name']] = $image;
                $out['nodes'][] = [
                    'name' => (string) $node['name'],
                    'image' => (string) ($image->title ?? ($node['image'] ?? '')),
                    'kind' => (string) ($image->kind ?? ''),
                    'category' => (string) ($image->category ?? ''),
                    'scripts' => $image !== null && ($image->kind ?? '') === 'qemu' && !isset($image->uplink_port),
                ];
            }
            $end = function (array $e) use ($byname): string {
                $format = (string) (($byname[$e['node']] ?? null)->port_name ?? '{adapter}/{port}');
                return trim($e['node'] . ' ' . str_replace(['{adapter}', '{port}'], [$e['adapter'], $e['port']], $format));
            };
            foreach ($lab['links'] ?? [] as $link) {
                $out['links'][] = ['a' => $end($link['a']), 'b' => $end($link['b'])];
            }
            $out['segments'] = array_values($lab['segments'] ?? []);
            $out['found'] = true;
        } else if (is_string($lab)) {
            try {
                $doc = \core\di::get(manager_client::class)->get_lab_topology($lab);
            } catch (manager_exception $e) {
                return $out;
            }
            $names = [];
            foreach ($doc->topology->nodes ?? [] as $node) {
                if (!is_object($node) || !is_string($node->name ?? null)) {
                    continue;
                }
                $type = (string) ($node->node_type ?? '');
                $names[(string) ($node->node_id ?? '')] = $node->name;
                $out['nodes'][] = ['name' => $node->name, 'image' => $type, 'kind' => $type, 'category' => '',
                    'scripts' => $type === 'qemu'];
            }
            foreach ($doc->topology->links ?? [] as $link) {
                $ends = [];
                foreach (is_object($link) && is_array($link->nodes ?? null) ? $link->nodes : [] as $e) {
                    $ends[] = trim(($names[(string) ($e->node_id ?? '')] ?? '?') . ' '
                        . (int) ($e->adapter_number ?? 0) . '/' . (int) ($e->port_number ?? 0));
                }
                if (count($ends) === 2) {
                    $out['links'][] = ['a' => $ends[0], 'b' => $ends[1]];
                }
            }
            $out['found'] = true;
        }
        return $out;
    }

    /** Problem codes of the lab manager the checks block names in words (ed_problem_<code>). */
    public const PROBLEM_CODES = [
        'required', 'unknown_field', 'bad_type', 'bad_name', 'duplicate_name', 'out_of_range', 'unknown_node',
        'too_large', 'unknown_rule', 'group_overlap', 'bad_range', 'script_node_unsupported', 'shell_mismatch',
        'bad_regex', 'unknown_reference', 'lab_not_found',
    ];

    /**
     * The texts of the checks block (checksform.js), all at once.
     *
     * @return array<string, string>
     */
    public static function strings(): array {
        $keys = ['ed_checks', 'ed_checks_help', 'ed_checkskept', 'ed_checksinvalid', 'ed_network', 'ed_network_auto',
            'ed_network_off', 'ed_network_help', 'chk_class_critical', 'chk_class_major', 'chk_class_minor', 'chk_class_note'];
        foreach (array_keys(get_string_manager()->load_component_strings('gns3lab', current_language())) as $key) {
            if (str_starts_with($key, 'ed_script') || str_starts_with($key, 'checksdef_')) {
                $keys[] = $key;
            }
        }
        $strings = [];
        foreach ($keys as $key) {
            $strings[$key] = get_string($key, 'gns3lab');
        }
        foreach (self::PROBLEM_CODES as $code) {
            $strings['problem_' . $code] = get_string('ed_problem_' . $code, 'gns3lab');
        }
        return $strings;
    }

    /**
     * Asks the lab manager whether a document fits a lab.
     *
     * @param string|array $lab what lab_of() gives
     * @param array $doc gns3lab.checks/1
     * @return array valid (bool), checksdef (hash, '' unless valid), problems [{path, code, message}], error (why the
     *     manager could not be asked, '' otherwise)
     */
    public static function validate(string|array $lab, array $doc): array {
        $out = ['valid' => false, 'checksdef' => '', 'problems' => [], 'error' => ''];
        try {
            $result = \core\di::get(manager_client::class)->validate_checks($lab, $doc);
        } catch (manager_exception $e) {
            if (in_array($e->remotecode, ['checks_invalid', 'lab_invalid'], true) && $e->httpstatus === 422) {
                $out['problems'] = \mod_gns3lab\external\lab_problems::from_exception($e);
            } else if ($e->remotecode === 'lab_not_found') {
                $out['problems'] = [['path' => '', 'code' => 'lab_not_found', 'message' => '']];
            } else {
                $out['error'] = $e->getMessage();
            }
            return $out;
        }
        return ['valid' => true, 'checksdef' => \core_text::substr((string) $result->checks_def, 0, 80), 'problems' => [],
            'error' => ''];
    }
}
