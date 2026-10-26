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

/**
 * Takes the commands and their output off the buffer of a terminal (docs/live-state.md §8.1).
 *
 * The buffer is read, not the stream: xterm.js has already applied Backspace, the history, redrawing of the line, colours
 * and the wrapping of long lines. A command is what stands after the prompt on the line where the prompt was seen, its
 * output is what lies between that line and the next prompt, which the cursor reaches when the stream has been silent
 * for a moment. Nothing is read from the keyboard, so the observer (a teacher who only looks at the console of a
 * student) gets the same commands. The watch only reads: it does not delay input or output and writes nothing.
 *
 * It uses a few members of an xterm.js Terminal: onWriteParsed, registerMarker and buffer.active.
 *
 * @module     mod_gns3lab/consolewatch
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

import {MAX_OUTPUT_LINES, promptKind} from 'mod_gns3lab/consolefacts';

/** The stream has to be silent this long, ms, before the line under the cursor is taken for the end of a command. */
export const QUIET = 300;

/** The pager of Cisco IOS: the output may be cut short (the student can press q). */
const MORE_RE = /--\s*More\s*--/i;

/**
 * Watches one terminal.
 */
export class ConsoleWatch {
    /**
     * Constructor.
     *
     * @param {object} term xterm.js Terminal
     * @param {object} options
     * @param {string} options.hint kind of the device by its node: ios, linux or vpcs
     * @param {Function} options.onCommand called with {kind, command, lines, partial} for every command seen
     * @param {number} [options.quiet] the silence that ends a command, ms
     */
    constructor(term, {hint, onCommand, quiet = QUIET}) {
        this.term = term;
        this.hint = hint;
        this.onCommand = onCommand;
        this.quiet = quiet;
        this.timer = null;
        // Where the last prompt was seen: a marker keeps the line while the buffer scrolls and is disposed of
        // with it, so a command whose lines have left the scrollback is skipped.
        this.marker = null;
        this.prompt = null;
        this.paged = false;
        this.disposed = false;
        this.sub = term.onWriteParsed(() => this.touch());
    }

    /**
     * Something was written to the terminal: note the pager and wait for silence.
     */
    touch() {
        if (this.disposed) {
            return;
        }
        if (this.marker && !this.paged) {
            const buf = this.term.buffer.active;
            const row = buf.getLine(buf.baseY + buf.cursorY);
            // The pager erases its own line when the student goes on, so only the moment it stands there tells.
            this.paged = !!row && MORE_RE.test(row.translateToString(true));
        }
        clearTimeout(this.timer);
        this.timer = setTimeout(() => this.settle(), this.quiet);
    }

    /**
     * The stream is silent: is the cursor on a prompt, and has a command ended since the previous one?
     */
    settle() {
        const buf = this.term.buffer.active;
        if (this.disposed || buf.type === 'alternate') {
            return;
        }
        const at = buf.baseY + buf.cursorY;
        const row = buf.getLine(at);
        if (!row || row.isWrapped || row.translateToString(true, buf.cursorX).trim() !== '') {
            return;
        }
        const text = row.translateToString(false, 0, buf.cursorX);
        const kind = promptKind(this.hint, text);
        if (!kind) {
            return;
        }
        const from = this.marker && !this.marker.isDisposed ? this.marker.line : -1;
        if (from === at) {
            return; // Still the same prompt.
        }
        if (from >= 0 && from < at && this.prompt) {
            this.finish(buf, from, at);
        }
        this.forget();
        this.marker = this.term.registerMarker(0) || null;
        this.prompt = {text, kind};
        this.paged = false;
    }

    /**
     * The command lines [from, to) ended with the prompt at `to`: tell what was run.
     *
     * @param {object} buf the buffer
     * @param {number} from line of the prompt the commands started at
     * @param {number} to line of the prompt they ended with
     */
    finish(buf, from, to) {
        if (to - from > MAX_OUTPUT_LINES + 1) {
            return;
        }
        // A wrapped line is one line of the device: the rows of it are joined.
        const rows = [];
        for (let i = from; i < to; i++) {
            const row = buf.getLine(i);
            if (!row) {
                return;
            }
            rows.push(row);
        }
        const texts = [];
        rows.forEach((row, k) => {
            const text = row.translateToString(!(rows[k + 1] && rows[k + 1].isWrapped));
            if (row.isWrapped && texts.length) {
                texts[texts.length - 1] += text;
            } else {
                texts.push(text);
            }
        });
        const {kind, text: prompt} = this.prompt;
        const head = prompt.trimEnd();
        if (!texts[0].startsWith(head)) {
            return; // The line was drawn over.
        }
        // Several commands that came faster than the silence last: each prompt-led line starts one.
        const host = /^[A-Za-z0-9._-]+/.exec(head);
        const ios = kind === 'ios' && host ? new RegExp('^' + host[0].replace(/\./g, '\\.') + '(\\([A-Za-z0-9-]+\\))?[>#]') : null;
        const led = line => (ios ? ios.test(line) : line.startsWith(head));
        const blocks = [];
        texts.forEach((line, k) => {
            if (k === 0 || led(line)) {
                blocks.push({head: line, lines: []});
            } else {
                blocks[blocks.length - 1].lines.push(line);
            }
        });
        for (const block of blocks) {
            const command = (ios ? block.head.replace(ios, '') : block.head.slice(head.length)).trim();
            while (block.lines.length && !block.lines[block.lines.length - 1].trim()) {
                block.lines.pop();
            }
            if (command) {
                this.onCommand({kind, command, lines: block.lines, partial: this.paged});
            }
        }
    }

    /**
     * Lets go of the marker of the last prompt.
     */
    forget() {
        if (this.marker) {
            this.marker.dispose();
        }
        this.marker = null;
        this.prompt = null;
    }

    /**
     * Stops watching.
     */
    dispose() {
        this.disposed = true;
        clearTimeout(this.timer);
        this.sub.dispose();
        this.forget();
    }
}
