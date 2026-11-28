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
 * The prompt for a neural network in the "Setup" block of the lab editor: it writes the teacher's setup steps of a lab.
 *
 * The text lives in prompts/setup.<lang>.md (docs/lab-setup/README.md §6a) and is read like the prompt of the checks
 * (checks_prompt): the leading HTML comments (the format line the lab manager's tests compare) are not shown.
 *
 * @package    mod_gns3lab
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
class setup_prompt {
    /**
     * The prompt in a language, else in English; '' if there is none.
     *
     * @param string $lang Moodle language code, e.g. ru or en_us
     * @return string text with [[LAB]], [[SETUP]] and [[TASK]] for the editor to fill in
     */
    public static function text(string $lang): string {
        return checks_prompt::read('setup', $lang);
    }
}
