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

/** Typed public catalog data; the manager's file paths and slot options never reach AJAX. */
class image_data {
    /** Catalog result schema. */
    public static function structure(): external_multiple_structure {
        $range = fn() => new external_single_structure([
            'default' => new external_value(PARAM_INT, 'Default'),
            'min' => new external_value(PARAM_INT, 'Minimum'),
            'max' => new external_value(PARAM_INT, 'Maximum'),
        ], 'Resource range', VALUE_DEFAULT, null);
        return new external_multiple_structure(new external_single_structure([
            'id' => new external_value(PARAM_ALPHANUMEXT, 'Image ID'),
            'title' => new external_value(PARAM_TEXT, 'Title'),
            'kind' => new external_value(PARAM_ALPHA, 'Node kind'),
            'category' => new external_value(PARAM_ALPHANUMEXT, 'Category'),
            'symbol' => new external_value(PARAM_TEXT, 'Symbol'),
            'console_type' => new external_value(PARAM_ALPHA, 'Console type'),
            'ram_mb' => $range(), 'cpus' => $range(), 'adapters' => $range(),
            'port_name' => new external_value(PARAM_TEXT, 'Port name format'),
            'ports_per_adapter' => new external_value(PARAM_INT, 'Ports per adapter'),
            'preconfig' => new external_value(PARAM_BOOL, 'Network can be configured'),
            'capture' => new external_value(PARAM_BOOL, 'Capture allowed'),
            'authorname' => new external_value(PARAM_TEXT, 'Moodle display name', VALUE_DEFAULT, ''),
            'featured' => new external_value(PARAM_BOOL, 'Featured', VALUE_DEFAULT, false),
            'retired' => new external_value(PARAM_BOOL, 'Retired', VALUE_DEFAULT, false),
            'derived' => new external_single_structure([
                'forced' => new external_value(PARAM_BOOL, 'Captured without confirmed guest shutdown', VALUE_DEFAULT, false),
                'base' => new external_value(PARAM_ALPHANUMEXT, 'Base ID'),
                'base_sha256' => new external_value(PARAM_TEXT, 'Base digest'),
                'sha256' => new external_value(PARAM_TEXT, 'Image digest'),
                'size' => new external_value(PARAM_INT, 'File size'),
                'author_user_id' => new external_value(PARAM_INT, 'Author'),
                'published_at' => new external_value(PARAM_TEXT, 'Publication time'),
                'description' => new external_value(PARAM_TEXT, 'Description'),
                'supersedes' => new external_value(PARAM_ALPHANUMEXT, 'Previous image', VALUE_DEFAULT, null),
                'source' => new external_single_structure([
                    'library_lab_id' => new external_value(PARAM_INT, 'Source lab'),
                    'node' => new external_value(PARAM_TEXT, 'Source node'),
                ]),
            ], 'Derived image metadata', VALUE_OPTIONAL),
        ]), 'Public images', VALUE_OPTIONAL);
    }

    /** Add the local display names requested by the library UI. */
    public static function export(array $images): array {
        $names = [];
        foreach ($images as $image) {
            if (isset($image->derived->author_user_id)) {
                $userid = (int) $image->derived->author_user_id;
                if (!array_key_exists($userid, $names)) {
                    $user = \core_user::get_user($userid);
                    $names[$userid] = $user ? fullname($user) : get_string('unknownuser');
                }
                $image->authorname = $names[$userid];
            }
            $image->capture = $image->capture ?? $image->kind === 'qemu';
        }
        return array_map(fn($image) => json_decode(json_encode($image), true), $images);
    }
}
