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
 * The prompt for a neural network in the lab editor: it writes the teacher's scripts of the auto-checks.
 *
 * The text lives in prompts/checks.<lang>.md (docs/autochecks/README.md §7.8). Its leading HTML comments are notes for
 * whoever edits it (the format it follows, which the lab manager's tests compare) and are not shown.
 *
 * @package    mod_gns3lab
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
class checks_prompt {
    /**
     * The prompt in a language, else in English; '' if there is none.
     *
     * @param string $lang Moodle language code, e.g. ru or en_us
     * @return string text with [[LAB]], [[CHECKS]] and [[TASK]] for the editor to fill in
     */
    public static function text(string $lang): string {
        return self::read('checks', $lang);
    }

    /**
     * A prompt of prompts/<name>.<lang>.md in a language, else in English, without its leading HTML comments.
     *
     * @param string $name checks or setup
     * @param string $lang Moodle language code
     * @return string the text, '' if there is none
     */
    public static function read(string $name, string $lang): string {
        $name = preg_replace('/[^a-z]/', '', $name);
        $lang = preg_replace('/[^a-z_]/', '', strtolower($lang));
        foreach (array_unique([$lang, explode('_', $lang)[0], 'en']) as $code) {
            $file = __DIR__ . '/../../prompts/' . $name . '.' . $code . '.md';
            if ($code !== '' && is_readable($file)) {
                return trim(preg_replace('/\A(?:<!--.*?-->\s*)+/s', '', (string) file_get_contents($file)));
            }
        }
        return '';
    }
}
