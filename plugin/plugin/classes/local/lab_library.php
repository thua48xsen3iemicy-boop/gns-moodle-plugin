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

use core_tag_tag;

/**
 * Site lab library: labs, their versions and the lab author role.
 *
 * A lab has at most one draft, which the editor saves freely, and frozen published versions. Publishing asks
 * the lab manager to check the definition and keeps the hash it returns (lab_def). An activity points to one
 * published version, so a new version never changes a running course until the teacher picks it.
 *
 * @package    mod_gns3lab
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
class lab_library {
    /** Short name of the lab author role. */
    public const AUTHOR_ROLE = 'gns3labauthor';

    /** Tag area item type. */
    public const TAG_ITEMTYPE = 'gns3lab_labs';

    /** Draft status of a version. */
    public const DRAFT = 'draft';

    /** Published status of a version. */
    public const PUBLISHED = 'published';

    /** Key of the library in the primary navigation. */
    public const NAV_KEY = 'gns3lablibrary';

    /**
     * Creates the lab author role (system level, may manage the lab library) unless it exists.
     *
     * Runs on install and upgrade, before core refreshes the capabilities, so it refreshes them itself.
     */
    public static function create_author_role(): void {
        global $DB;

        update_capabilities('mod_gns3lab');
        $roleid = $DB->get_field('role', 'id', ['shortname' => self::AUTHOR_ROLE]);
        if (!$roleid) {
            $roleid = create_role(
                get_string('labauthorrole', 'gns3lab'),
                self::AUTHOR_ROLE,
                get_string('labauthorrole_desc', 'gns3lab')
            );
            set_role_contextlevels($roleid, [CONTEXT_SYSTEM]);
        }
        assign_capability('mod/gns3lab:managelabs', CAP_ALLOW, $roleid, \context_system::instance()->id, true);
    }

    /**
     * Sets up a page of the library for lab authors, after require_login(): capability, context and navigation.
     *
     * @param \core\url $url page URL
     * @param string $layout page layout
     * @return \context_system
     */
    public static function setup_page(\core\url $url, string $layout = 'standard'): \context_system {
        global $PAGE;

        $context = self::context();
        require_capability('mod/gns3lab:managelabs', $context);
        $PAGE->set_context($context);
        $PAGE->set_url($url);
        $PAGE->set_pagelayout($layout);
        $PAGE->set_title(get_string('library', 'gns3lab'));
        $PAGE->set_heading(get_string('library', 'gns3lab'));
        $PAGE->set_primary_active_tab(self::NAV_KEY);
        $PAGE->navbar->add(get_string('library', 'gns3lab'), new \core\url('/mod/gns3lab/library.php'));
        return $context;
    }

    /**
     * Context of the library: labs belong to the site.
     *
     * @return \context_system
     */
    public static function context(): \context_system {
        return \context_system::instance();
    }

    /**
     * Creates a lab with an empty draft.
     *
     * @param \stdClass $data name, description, descriptionformat, tags (optional)
     * @return int lab id
     */
    public static function create_lab(\stdClass $data): int {
        global $DB, $USER;

        $now = time();
        $labid = $DB->insert_record('gns3lab_labs', (object) [
            'name' => trim($data->name),
            'description' => $data->description ?? '',
            'descriptionformat' => (int) ($data->descriptionformat ?? FORMAT_HTML),
            'usercreated' => $USER->id,
            'usermodified' => $USER->id,
            'timecreated' => $now,
            'timemodified' => $now,
        ]);
        if (isset($data->tags)) {
            core_tag_tag::set_item_tags('mod_gns3lab', self::TAG_ITEMTYPE, $labid, self::context(), $data->tags);
        }
        self::save_draft($labid, lab_definition::empty(trim($data->name)));
        return $labid;
    }

    /**
     * Updates the name, the description and the tags of a lab.
     *
     * @param \stdClass $data id, name, description, descriptionformat, tags (optional)
     */
    public static function update_lab(\stdClass $data): void {
        global $DB, $USER;

        $lab = self::get_lab((int) $data->id);
        $DB->update_record('gns3lab_labs', (object) [
            'id' => $lab->id,
            'name' => trim($data->name),
            'description' => $data->description ?? '',
            'descriptionformat' => (int) ($data->descriptionformat ?? FORMAT_HTML),
            'usermodified' => $USER->id,
            'timemodified' => time(),
        ]);
        if (isset($data->tags)) {
            core_tag_tag::set_item_tags('mod_gns3lab', self::TAG_ITEMTYPE, $lab->id, self::context(), $data->tags);
        }
    }

    /**
     * Deletes a lab that no activity uses.
     *
     * @param int $labid lab id
     * @throws \moodle_exception labinuse
     */
    public static function delete_lab(int $labid): void {
        global $DB;

        $lab = self::get_lab($labid);
        if (self::usage([$lab->id])[$lab->id] ?? 0) {
            throw new \moodle_exception('labinuse', 'gns3lab');
        }
        $transaction = $DB->start_delegated_transaction();
        core_tag_tag::remove_all_item_tags('mod_gns3lab', self::TAG_ITEMTYPE, $lab->id);
        $DB->delete_records('gns3lab_lab_versions', ['labid' => $lab->id]);
        $DB->delete_records('gns3lab_labs', ['id' => $lab->id]);
        $transaction->allow_commit();
    }

    /**
     * A lab.
     *
     * @param int $labid lab id
     * @return \stdClass
     */
    public static function get_lab(int $labid): \stdClass {
        global $DB;
        return $DB->get_record('gns3lab_labs', ['id' => $labid], '*', MUST_EXIST);
    }

    /**
     * A version of a lab.
     *
     * @param int $versionid version id
     * @return \stdClass
     */
    public static function get_version(int $versionid): \stdClass {
        global $DB;
        return $DB->get_record('gns3lab_lab_versions', ['id' => $versionid], '*', MUST_EXIST);
    }

    /**
     * The version the editor opens: the draft, or else the last published version.
     *
     * @param int $labid lab id
     * @return \stdClass|null
     */
    public static function latest_version(int $labid): ?\stdClass {
        global $DB;

        $versions = $DB->get_records('gns3lab_lab_versions', ['labid' => $labid], 'version DESC', '*', 0, 1);
        return $versions ? reset($versions) : null;
    }

    /**
     * The last published version of a lab.
     *
     * @param int $labid lab id
     * @return \stdClass|null
     */
    public static function last_published(int $labid): ?\stdClass {
        global $DB;

        $versions = $DB->get_records(
            'gns3lab_lab_versions',
            ['labid' => $labid, 'status' => self::PUBLISHED],
            'version DESC',
            '*',
            0,
            1
        );
        return $versions ? reset($versions) : null;
    }

    /**
     * Saves the draft of a lab, creating it after the last published version if there is none.
     *
     * @param int $labid lab id
     * @param array $def clean definition
     * @return \stdClass the draft
     */
    public static function save_draft(int $labid, array $def): \stdClass {
        global $DB, $USER;

        $now = time();
        $latest = self::latest_version($labid);
        if ($latest && $latest->status === self::DRAFT) {
            $latest->definition = lab_definition::to_json($def);
            $latest->usermodified = $USER->id;
            $latest->timemodified = $now;
            $DB->update_record('gns3lab_lab_versions', $latest);
            $draft = $latest;
        } else {
            $draft = (object) [
                'labid' => $labid,
                'version' => $latest ? $latest->version + 1 : 1,
                'status' => self::DRAFT,
                'definition' => lab_definition::to_json($def),
                'labdef' => null,
                'usermodified' => $USER->id,
                'timecreated' => $now,
                'timemodified' => $now,
            ];
            $draft->id = $DB->insert_record('gns3lab_lab_versions', $draft);
        }
        $DB->set_field('gns3lab_labs', 'timemodified', $now, ['id' => $labid]);
        return $draft;
    }

    /**
     * Publishes a definition: the lab manager checks it, then the draft becomes a frozen version.
     *
     * If the lab manager finds problems, manager_exception lab_invalid carries them in details->problems and
     * the draft is saved anyway. Publishing what the last published version already has publishes nothing.
     *
     * @param int $labid lab id
     * @param array $def clean definition
     * @return \stdClass the published version
     */
    public static function publish(int $labid, array $def): \stdClass {
        global $DB;

        $lab = self::get_lab($labid);
        $def['title'] = $lab->name;
        $draft = self::save_draft($lab->id, $def);
        $result = \core\di::get(manager_client::class)->validate_lab($def);

        $last = self::last_published($lab->id);
        if ($last && $last->labdef === $result->lab_def) {
            $DB->delete_records('gns3lab_lab_versions', ['id' => $draft->id]);
            return $last;
        }
        $draft->status = self::PUBLISHED;
        $draft->labdef = \core_text::substr($result->lab_def, 0, 80);
        $draft->memorymb = max(0, (int) ($result->resources->memory_mb ?? 0));
        $draft->cores = max(0, (int) ($result->resources->cores ?? 0));
        $draft->timepublished = time();
        $DB->update_record('gns3lab_lab_versions', $draft);
        return $draft;
    }

    /**
     * Labs of the library with their tags, versions and how many activities use them.
     *
     * @param string $tag only labs with this tag, empty for all
     * @return \stdClass[] labs with tags (string[]), draft (\stdClass|null), published (\stdClass|null), usage
     */
    public static function list_labs(string $tag = ''): array {
        global $DB;

        $labs = $DB->get_records('gns3lab_labs', null, 'name ASC, id ASC');
        if (!$labs) {
            return [];
        }
        $tags = core_tag_tag::get_items_tags('mod_gns3lab', self::TAG_ITEMTYPE, array_keys($labs));
        $usage = self::usage(array_keys($labs));
        $versions = $DB->get_records(
            'gns3lab_lab_versions',
            null,
            'version ASC',
            'id, labid, version, status, timemodified, timepublished'
        );
        foreach ($labs as $lab) {
            $lab->tags = array_values(array_map(fn($t) => $t->get_display_name(false), $tags[$lab->id] ?? []));
            $lab->draft = null;
            $lab->published = null;
            $lab->usage = $usage[$lab->id] ?? 0;
        }
        foreach ($versions as $version) {
            if (isset($labs[$version->labid])) {
                $labs[$version->labid]->{$version->status === self::DRAFT ? 'draft' : 'published'} = $version;
            }
        }
        if ($tag !== '') {
            $labs = array_filter($labs, fn($lab) => in_array(\core_text::strtolower($tag), array_map(
                fn($t) => \core_text::strtolower($t),
                $lab->tags
            ), true));
        }
        return array_values($labs);
    }

    /**
     * Number of activities that use a version of each lab.
     *
     * @param int[] $labids lab ids
     * @return array<int, int> lab id => activities
     */
    public static function usage(array $labids): array {
        global $DB;

        if (!$labids) {
            return [];
        }
        [$insql, $params] = $DB->get_in_or_equal($labids);
        return array_map('intval', $DB->get_records_sql_menu(
            "SELECT v.labid, COUNT(g.id)
               FROM {gns3lab_lab_versions} v
               JOIN {gns3lab} g ON g.labversionid = v.id
              WHERE v.labid $insql
           GROUP BY v.labid",
            $params
        ));
    }

    /**
     * What an activity runs: its published library version or its reference lab on the manager.
     *
     * @param \stdClass $instance gns3lab record
     * @return string|array|null lab_ref, lab definition, or null if the activity has no lab
     */
    public static function activity_lab(\stdClass $instance): string|array|null {
        global $DB;

        if (!empty($instance->labversionid)) {
            $json = $DB->get_field(
                'gns3lab_lab_versions',
                'definition',
                ['id' => $instance->labversionid, 'status' => self::PUBLISHED]
            );
            return $json === false ? null : lab_definition::decode($json);
        }
        $labref = trim((string) ($instance->labref ?? ''));
        return $labref === '' ? null : $labref;
    }

    /**
     * Published versions for the activity form, newest first within each lab.
     *
     * @return array<int, string> version id => "lab name (version N)"
     */
    public static function published_menu(): array {
        global $DB;

        $rows = $DB->get_records_sql(
            "SELECT v.id, v.version, l.name
               FROM {gns3lab_lab_versions} v
               JOIN {gns3lab_labs} l ON l.id = v.labid
              WHERE v.status = :status
           ORDER BY l.name ASC, l.id ASC, v.version DESC",
            ['status' => self::PUBLISHED]
        );
        $menu = [];
        foreach ($rows as $row) {
            $menu[$row->id] = get_string('labversionname', 'gns3lab', [
                'name' => format_string($row->name, true, ['context' => self::context()]),
                'version' => $row->version,
            ]);
        }
        return $menu;
    }
}
