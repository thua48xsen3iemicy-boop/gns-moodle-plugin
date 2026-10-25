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
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
// GNU General Public License for more details.

/**
 * Privacy provider for the standalone topology preview.
 *
 * @package    local_gns3isoflow
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

namespace local_gns3isoflow\privacy;

use core_privacy\local\metadata\null_provider;

/**
 * The preview does not store uploaded files or user data.
 */
class provider implements null_provider {
    /**
     * Explain why this plugin has no Moodle user data.
     *
     * @return string
     */
    public static function get_reason(): string {
        return get_string('privacy:metadata', 'local_gns3isoflow');
    }
}
